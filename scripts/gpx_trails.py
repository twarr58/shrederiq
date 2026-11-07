#!/usr/bin/env python3
"""Lajny z ručně stažených GPX (např. z Trailforks) pro parky, které v OSM chybí.

Soubory: data/gpx/<park_id>/*.gpx – jeden soubor může mít víc <trk>, každý je jedna lajna.
  název      <trk><name>, jinak název souboru bez přípony,
  obtížnost  předpona souboru „red__Flow.gpx“ (green / blue / red / black),
             jinak <trk><type>, jinak <trk><desc> obsahující některé z těch slov,
  zdroj      „gpx“, u Trailforks (poznáme z <creator> nebo <link>) „trailforks“.

Výstup má stejný tvar jako lajny z OSM (park_id, name, difficulty, access, length_m,
source), takže je sklon (elevation.py) i web zpracují stejně.

  python3 scripts/gpx_trails.py --merge web/data/trails.geojson   # připojí k lajnám z OSM
  python3 scripts/gpx_trails.py --out gpx.geojson                 # jen GPX
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import DIFFICULTIES, PARKS, haversine_m, simplify  # noqa: E402

GPX_DIR = Path(__file__).resolve().parent.parent / "data" / "gpx"
NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
DIFF_RE = re.compile(r"\b(green|blue|red|black)\b", re.I)
PREFIX_RE = re.compile(r"^(green|blue|red|black)__", re.I)


def _text(el, tag: str) -> str | None:
    found = el.find(f"gpx:{tag}", NS)
    if found is None:
        found = el.find(tag)  # GPX bez namespace
    return (found.text or "").strip() if found is not None and found.text else None


def _difficulty(filename: str, trk) -> str | None:
    m = PREFIX_RE.match(filename)
    if m:
        return m.group(1).lower()
    for tag in ("type", "desc", "cmt"):
        value = _text(trk, tag)
        if value:
            m = DIFF_RE.search(value)
            if m and m.group(1).lower() in DIFFICULTIES:
                return m.group(1).lower()
    return None


def _source(root) -> str:
    creator = (root.get("creator") or "").lower()
    links = " ".join((l.get("href") or "") for l in root.iter() if l.tag.endswith("link")).lower()
    return "trailforks" if "trailforks" in creator or "trailforks" in links else "gpx"


def parse_gpx(path: Path, park_id: str) -> list[dict]:
    """GPX soubor → seznam GeoJSON Feature (LineString) jedné lajny na <trk>."""
    root = ET.parse(path).getroot()
    source = _source(root)
    tracks = root.findall("gpx:trk", NS) or root.findall("trk")
    features = []
    for i, trk in enumerate(tracks):
        coords = []
        for seg in trk.findall("gpx:trkseg", NS) or trk.findall("trkseg"):
            for pt in seg.findall("gpx:trkpt", NS) or seg.findall("trkpt"):
                coords.append((float(pt.get("lat")), float(pt.get("lon"))))
        if len(coords) < 2:
            continue
        name = _text(trk, "name") or PREFIX_RE.sub("", path.stem)
        if len(tracks) > 1 and not _text(trk, "name"):
            name = f"{name} {i + 1}"
        length = sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in simplify(coords)]},
            "properties": {"park_id": park_id, "name": name, "difficulty": _difficulty(path.name, trk),
                           "access": False, "length_m": round(length), "source": source,
                           "file": f"{park_id}/{path.name}"},
        })
    return features


def load_all(gpx_dir: Path = GPX_DIR, park_ids: set[str] | None = None) -> list[dict]:
    features = []
    if not gpx_dir.is_dir():
        return features
    for park_dir in sorted(p for p in gpx_dir.iterdir() if p.is_dir()):
        if park_ids is not None and park_dir.name not in park_ids:
            print(f"gpx: {park_dir.name} není v parks.geojson, přeskakuji", file=sys.stderr)
            continue
        for path in sorted(park_dir.glob("*.gpx")):
            try:
                features.extend(parse_gpx(path, park_dir.name))
            except ET.ParseError as e:
                print(f"gpx: {path}: {e}", file=sys.stderr)
    return features


def merge(trails_fc: dict, features: list[dict], replace_key: str = "file") -> dict:
    """Připojí lajny k lajnám z OSM. Dříve připojené lajny stejného původu (ty s vlastností
    replace_key: GPX mají "file", Trailforks API "trailforks_id") nejdřív odstraní, aby šel
    skript pouštět opakovaně; lajny z OSM i druhého zdroje nechá."""
    kept = [f for f in trails_fc.get("features", []) if not f["properties"].get(replace_key)]
    out = dict(trails_fc)
    out["features"] = kept + features
    if any(f["properties"].get("source") == "trailforks" for f in out["features"]):
        out["attribution_gpx"] = "Lajny © Trailforks (trailforks.com), jen pro nekomerční použití"
    else:
        out.pop("attribution_gpx", None)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gpx-dir", type=Path, default=GPX_DIR)
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--merge", type=Path, help="trails.geojson, do kterého se GPX lajny připojí (vytvoří se, když chybí)")
    ap.add_argument("--out", type=Path, help="jen GPX lajny jako GeoJSON")
    args = ap.parse_args(argv)

    park_ids = {f["properties"]["id"] for f in json.loads(args.parks.read_text())["features"]} if args.parks.exists() else None
    gpx = load_all(args.gpx_dir, park_ids)
    by_park: dict[str, int] = {}
    for f in gpx:
        by_park[f["properties"]["park_id"]] = by_park.get(f["properties"]["park_id"], 0) + 1
    print(f"gpx: {len(gpx)} lajn z {len(by_park)} parků" + (f" ({', '.join(f'{k}: {v}' for k, v in sorted(by_park.items()))})" if by_park else ""),
          file=sys.stderr)

    if args.out:
        args.out.write_text(json.dumps({"type": "FeatureCollection", "features": gpx}, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.merge:
        base = json.loads(args.merge.read_text()) if args.merge.exists() else \
            {"type": "FeatureCollection", "attribution": "© přispěvatelé OpenStreetMap, ODbL", "features": []}
        args.merge.write_text(json.dumps(merge(base, gpx), ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
