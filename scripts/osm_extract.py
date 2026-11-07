#!/usr/bin/env python3
"""Vytáhne z OSM výřezu (.osm.pbf, např. Geofabrik czech-republic-latest.osm.pbf)
data kolem parků ve stejném tvaru jako odpověď Overpass, kterou zpracuje park_osm.py.

Overpass je často přetížený (502/504, tiché vynechání částí dotazu). Výřez od
Geofabriku je denně aktualizovaný soubor – zpracování je spolehlivé a opakovatelné.

Doporučený postup (v GitHub Actions):
  curl -sSfLo cz.osm.pbf https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf
  osmium tags-filter cz.osm.pbf w/mtb:scale w/mtb:scale:imba w/mtb:scale:dh w/mtb=designated \\
      w/bicycle=designated w/aerialway nwr/amenity=parking r/route=mtb -o mtb.osm.pbf
  python3 scripts/osm_extract.py mtb.osm.pbf --out parks-osm.json
  python3 scripts/park_osm.py --cache parks-osm.json --trails … --info …

(tags-filter ponechá i uzly a členy relací, na které vybrané objekty odkazují.)

Potřebuje pyosmium: pip install osmium
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import PARKS, haversine_m  # noqa: E402
from park_osm import LIFTS, RADIUS_M  # noqa: E402

TRAIL_HIGHWAYS = {"path", "track", "cycleway", "bridleway", "footway"}
LIFT_TYPES = set(LIFTS.split("|"))
MARGIN_M = 500  # rezerva navíc k RADIUS_M, ať se neuřízne cesta, která do okruhu jen zasahuje


def _tags(obj) -> dict:
    return {t.k: t.v for t in obj.tags}


def is_trail(tags: dict) -> bool:
    hw = tags.get("highway")
    return (("mtb:scale" in tags and hw in TRAIL_HIGHWAYS)
            or "mtb:scale:imba" in tags or "mtb:scale:dh" in tags
            or (hw == "path" and (tags.get("mtb") == "designated" or tags.get("bicycle") == "designated")))


class Near:
    """Rychlý test „je bod do R metrů od některého parku“ (nejdřív obdélník, pak vzdálenost)."""

    def __init__(self, parks: list[dict], radius_m: float):
        self.r = radius_m
        self.pts = [(f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) for f in parks]
        dlat = radius_m / 111_000
        self.boxes = [(lat - dlat, lat + dlat, lon - dlat / math.cos(math.radians(lat)),
                       lon + dlat / math.cos(math.radians(lat))) for lat, lon in self.pts]

    def __call__(self, lat: float, lon: float) -> bool:
        for (la, lo), (s, n, w, e) in zip(self.pts, self.boxes):
            if s <= lat <= n and w <= lon <= e and haversine_m(lat, lon, la, lo) <= self.r:
                return True
        return False


def extract(pbf: str, parks: list[dict], radius_m: float = RADIUS_M + MARGIN_M) -> dict:
    import osmium  # pyosmium – jen pro tenhle krok

    near = Near(parks, radius_m)

    class Relations(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.rels = {}

        def relation(self, r):
            tags = _tags(r)
            if tags.get("route") == "mtb":
                self.rels[r.id] = {"tags": tags, "members": [m.ref for m in r.members if m.type == "w"]}

    rh = Relations()
    rh.apply_file(pbf)
    member_ways = {w for rel in rh.rels.values() for w in rel["members"]}

    class Objects(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.elements = []
            self.kept_ways = set()

        def node(self, n):
            if n.tags.get("amenity") == "parking" and n.location.valid() and near(n.location.lat, n.location.lon):
                self.elements.append({"type": "node", "id": n.id, "lat": n.location.lat, "lon": n.location.lon,
                                      "tags": _tags(n)})

        def way(self, w):
            tags = _tags(w)
            trail, lift = is_trail(tags) or w.id in member_ways, tags.get("aerialway") in LIFT_TYPES
            parking = tags.get("amenity") == "parking"
            if not (trail or lift or parking):
                return
            try:
                geom = [(nd.location.lat, nd.location.lon) for nd in w.nodes if nd.location.valid()]
            except osmium.InvalidLocationError:
                return
            if len(geom) < 2 or not any(near(lat, lon) for lat, lon in geom):
                return
            if parking:
                lat = sum(p[0] for p in geom) / len(geom)
                lon = sum(p[1] for p in geom) / len(geom)
                self.elements.append({"type": "way", "id": w.id, "center": {"lat": lat, "lon": lon}, "tags": tags})
                return
            self.kept_ways.add(w.id)
            self.elements.append({"type": "way", "id": w.id, "tags": tags,
                                  "geometry": [{"lat": round(a, 7), "lon": round(b, 7)} for a, b in geom]})

    oh = Objects()
    oh.apply_file(pbf, locations=True)
    for rid, rel in rh.rels.items():
        if any(m in oh.kept_ways for m in rel["members"]):
            oh.elements.append({"type": "relation", "id": rid, "tags": rel["tags"],
                                "members": [{"type": "way", "ref": m, "role": ""} for m in rel["members"]]})
    return {"generator": "shrederiq osm_extract", "elements": oh.elements}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", help="OSM výřez (.osm.pbf nebo .osm)")
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--out", type=Path, required=True, help="JSON ve tvaru odpovědi Overpass")
    args = ap.parse_args(argv)

    parks = json.loads(args.parks.read_text())["features"]
    data = extract(args.pbf, parks)
    counts = {}
    for e in data["elements"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"osm_extract: {counts}", file=sys.stderr)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
