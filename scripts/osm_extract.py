#!/usr/bin/env python3
"""Vytáhne z OSM výřezu (.osm.pbf, např. Geofabrik czech-republic-latest.osm.pbf)
data kolem parků ve stejném tvaru jako odpověď Overpass, kterou zpracuje park_osm.py.

Overpass je často přetížený (502/504, tiché vynechání částí dotazu). Výřez od
Geofabriku je denně aktualizovaný soubor – zpracování je spolehlivé a opakovatelné.

Doporučený postup (v GitHub Actions):
  curl -sSfLo cz.osm.pbf https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf
  osmium tags-filter cz.osm.pbf w/mtb:scale w/mtb:scale:imba w/mtb:scale:dh w/mtb \\
      w/bicycle=designated w/aerialway nwr/amenity=parking r/route=mtb -o mtb.osm.pbf
  python3 scripts/osm_extract.py mtb.osm.pbf --out parks-osm.json --pumptracks web/data/pumptracks.geojson \\
      --country country.json   # celá ČR pro fetch_osm_parks.py --cache country.json
  python3 scripts/park_osm.py --cache parks-osm.json --trails … --info …

(tags-filter ponechá i uzly a členy relací, na které vybrané objekty odkazují.)

Potřebuje pyosmium: pip install osmium
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import PARKS, haversine_m, is_purpose_built  # noqa: E402
from park_osm import LIFTS, RADIUS_M  # noqa: E402

TRAIL_HIGHWAYS = {"path", "track", "cycleway", "bridleway", "footway"}
LIFT_TYPES = set(LIFTS.split("|"))
MARGIN_M = 500  # rezerva navíc k RADIUS_M, ať se neuřízne cesta, která do okruhu jen zasahuje


def _tags(obj) -> dict:
    return {t.k: t.v for t in obj.tags}


PUMP_RE = re.compile(r"pump ?track|pumptrek", re.I)


def is_pumptrack(tags: dict) -> bool:
    """sport=pump_track, nebo dráha / hřiště pro BMX či cyklistiku, nebo cokoli s „pumptrack“ v názvu."""
    sport = tags.get("sport") or ""
    name = tags.get("name") or ""
    if "pump_track" in sport or PUMP_RE.search(name):
        return True
    return tags.get("leisure") in ("track", "pitch") and any(x in sport for x in ("bmx", "cycling"))


def is_trail(tags: dict) -> bool:
    hw = tags.get("highway")
    return (("mtb:scale" in tags and hw in TRAIL_HIGHWAYS)
            or "mtb:scale:imba" in tags or "mtb:scale:dh" in tags
            or (hw == "path" and (tags.get("mtb") not in (None, "no") or tags.get("bicycle") == "designated"))
            or is_purpose_built(tags))


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


def extract_pumptracks(pbf: str) -> dict:
    """Pumptracky v celém výřezu jako GeoJSON body (uzly, nebo těžiště cest / ploch)."""
    import osmium

    feats = []

    def add(osm_type, osm_id, lat, lon, tags):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {"name": tags.get("name"), "osm": f"{osm_type}/{osm_id}", "sport": tags.get("sport"),
                           "surface": tags.get("surface"), "website": tags.get("website") or tags.get("contact:website")},
        })

    class Pumps(osmium.SimpleHandler):
        def node(self, n):
            tags = _tags(n)
            if is_pumptrack(tags) and n.location.valid():
                add("node", n.id, n.location.lat, n.location.lon, tags)

        def way(self, w):
            tags = _tags(w)
            if not is_pumptrack(tags):
                return
            try:
                pts = [(nd.location.lat, nd.location.lon) for nd in w.nodes if nd.location.valid()]
            except osmium.InvalidLocationError:
                return
            if pts:
                add("way", w.id, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), tags)

    h = Pumps()
    h.apply_file(pbf, locations=True)
    feats.sort(key=lambda f: (f["properties"]["name"] or "~", f["properties"]["osm"]))
    return {"type": "FeatureCollection", "attribution": "© přispěvatelé OpenStreetMap, ODbL", "features": feats}


ANCHOR_SPORTS = {"mtb", "downhill", "bmx"}
ANCHOR_LEISURE = {"sports_centre", "track", "pitch"}
ROUTE_NAME_RE = re.compile(r"[Ss]ingl|[Ss]ingle|[Tt]rail|[Bb]ike ?park|[Ff]low|[Ee]ndur")
LIFT_RADIUS_M = 1500


def extract_country(pbf: str) -> dict:
    """Celá ČR ve tvaru odpovědi Overpass pro fetch_osm_parks.py (celorepublikový report):
    lajny (mtb:scale:dh, mtb:scale:imba, path+mtb=designated) a MTB cesty do 1,5 km od
    lanovek pro kola, obce (place=town|village), kotvy (lanovky, leisure=bike_park,
    sportoviště mtb/downhill/bmx, relace route=mtb s trailovým názvem) se středem.
    Nahrazuje Overpass, který byl pomalý a odpovědi občas neúplné."""
    import osmium

    # 1. lanovky a relace (členové) – lanovky potřebujeme dřív než cesty kvůli okruhu 1,5 km
    class Pass1(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.lift_pts: list[tuple[float, float]] = []
            self.lifts: list[dict] = []
            self.rels: dict[int, dict] = {}

        def way(self, w):
            tags = _tags(w)
            if tags.get("aerialway") in LIFT_TYPES - {"drag_lift", "t-bar", "j-bar", "platter"}:
                try:
                    pts = [(nd.location.lat, nd.location.lon) for nd in w.nodes if nd.location.valid()]
                except osmium.InvalidLocationError:
                    return
                if len(pts) >= 2:
                    self.lift_pts += pts
                    self.lifts.append({"type": "way", "id": w.id, "tags": tags,
                                       "center": {"lat": sum(p[0] for p in pts) / len(pts), "lon": sum(p[1] for p in pts) / len(pts)}})

        def relation(self, r):
            tags = _tags(r)
            if tags.get("route") == "mtb" and ROUTE_NAME_RE.search(tags.get("name") or ""):
                self.rels[r.id] = {"tags": tags, "members": [m.ref for m in r.members if m.type == "w"]}

    p1 = Pass1()
    p1.apply_file(pbf, locations=True)
    near_lift = Near([{"geometry": {"coordinates": [lon, lat]}} for lat, lon in p1.lift_pts], LIFT_RADIUS_M) if p1.lift_pts else (lambda la, lo: False)
    member_of = {w: rid for rid, rel in p1.rels.items() for w in rel["members"]}

    class Pass2(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.elements: list[dict] = list(p1.lifts)
            self.rel_pts: dict[int, list[tuple[float, float]]] = {}

        def node(self, n):
            tags = _tags(n)
            if tags.get("place") in ("town", "village") and tags.get("name") and n.location.valid():
                self.elements.append({"type": "node", "id": n.id, "lat": n.location.lat, "lon": n.location.lon, "tags": tags})
            elif tags.get("leisure") == "bike_park" or (tags.get("leisure") in ANCHOR_LEISURE and _sporty(tags)):
                if n.location.valid():
                    self.elements.append({"type": "node", "id": n.id, "lat": n.location.lat, "lon": n.location.lon, "tags": tags})

        def way(self, w):
            tags = _tags(w)
            hw = tags.get("highway")
            trail = ("mtb:scale:dh" in tags or "mtb:scale:imba" in tags or (hw == "path" and tags.get("mtb") == "designated")
                     or (hw in ("path", "track") and "mtb:scale" in tags))
            anchor = tags.get("leisure") == "bike_park" or (tags.get("leisure") in ANCHOR_LEISURE and _sporty(tags))
            if not (trail or anchor or w.id in member_of):
                return
            try:
                geom = [(nd.location.lat, nd.location.lon) for nd in w.nodes if nd.location.valid()]
            except osmium.InvalidLocationError:
                return
            if len(geom) < 2:
                return
            if w.id in member_of:
                self.rel_pts.setdefault(member_of[w.id], []).extend(geom)
            if anchor:
                self.elements.append({"type": "way", "id": w.id, "tags": tags,
                                      "center": {"lat": sum(p[0] for p in geom) / len(geom), "lon": sum(p[1] for p in geom) / len(geom)}})
                return
            if trail and ("mtb:scale:dh" in tags or "mtb:scale:imba" in tags or tags.get("mtb") == "designated"
                          or any(near_lift(lat, lon) for lat, lon in geom[::3] + [geom[-1]])):
                self.elements.append({"type": "way", "id": w.id, "tags": tags,
                                      "geometry": [{"lat": round(a, 7), "lon": round(b, 7)} for a, b in geom]})

    p2 = Pass2()
    p2.apply_file(pbf, locations=True)
    for rid, rel in p1.rels.items():
        pts = p2.rel_pts.get(rid)
        if pts:
            p2.elements.append({"type": "relation", "id": rid, "tags": rel["tags"],
                                "center": {"lat": sum(p[0] for p in pts) / len(pts), "lon": sum(p[1] for p in pts) / len(pts)}})
    return {"generator": "shrederiq osm_extract --country", "elements": p2.elements}


def _sporty(tags: dict) -> bool:
    return bool(ANCHOR_SPORTS & set((tags.get("sport") or "").split(";")))


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
    ap.add_argument("--pumptracks", type=Path, help="pumptracky z celého výřezu jako GeoJSON body")
    ap.add_argument("--country", type=Path, help="celá ČR ve tvaru Overpass pro fetch_osm_parks.py (celorepublikový report)")
    args = ap.parse_args(argv)

    parks = json.loads(args.parks.read_text())["features"]
    data = extract(args.pbf, parks)
    counts = {}
    for e in data["elements"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"osm_extract: {counts}", file=sys.stderr)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    if args.pumptracks:
        pumps = extract_pumptracks(args.pbf)
        print(f"osm_extract: {len(pumps['features'])} pumptracků", file=sys.stderr)
        args.pumptracks.write_text(json.dumps(pumps, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.country:
        country = extract_country(args.pbf)
        kinds = {}
        for e in country["elements"]:
            k = "lajna" if "geometry" in e else ("obec" if e.get("tags", {}).get("place") else "kotva")
            kinds[k] = kinds.get(k, 0) + 1
        print(f"osm_extract: celá ČR {kinds}", file=sys.stderr)
        args.country.write_text(json.dumps(country, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
