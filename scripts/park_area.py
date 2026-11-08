#!/usr/bin/env python3
"""Vypíše GeoJSON MultiPolygon: čtverec kolem každého parku z web/data/parks.geojson.
Pro `osmium extract -p` v osm_extract.sh (pěšiny jen v okolí parků, ne z celé ČR).

  python3 scripts/park_area.py [--parks web/data/parks.geojson] [--radius 3500]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import PARKS  # noqa: E402
from osm_extract import MARGIN_M  # noqa: E402
from park_osm import RADIUS_M  # noqa: E402


def area(parks: list[dict], radius_m: float) -> dict:
    polys = []
    for f in parks:
        lon, lat = f["geometry"]["coordinates"]
        dlat = radius_m / 111_320
        dlon = radius_m / (111_320 * math.cos(math.radians(lat)))
        ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]
        polys.append([[[round(x, 5), round(y, 5)] for x, y in ring]])
    return {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": polys}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--radius", type=float, default=RADIUS_M + MARGIN_M)
    args = ap.parse_args(argv)
    print(json.dumps(area(json.loads(args.parks.read_text())["features"], args.radius)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
