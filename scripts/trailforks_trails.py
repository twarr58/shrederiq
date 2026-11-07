#!/usr/bin/env python3
"""Lajny parků z Trailforks API (druhá část #18) – běží jen s klíčem aplikace.

Park dostane v parks.geojson vlastnost "trailforks_rid" (id regionu na Trailforks, číslo
v URL regionu nebo v odpovědi /api/1/regions). Skript pro každý takový park stáhne lajny
regionu (/api/1/trails?filter=rid::<rid>&scope=full) a připojí je k web/data/trails.geojson
ve stejném tvaru jako lajny z OSM a GPX (source "trailforks").

Klíč: TRAILFORKS_APP_ID a TRAILFORKS_APP_SECRET v prostředí (v Actions jako secrets).
Bez klíče skript nic nestáhne a skončí s 0, build pokračuje bez Trailforks.

Obtížnost Trailforks → naše: 2–3 green, 4 blue, 5 red (black diamond), 6 a 8 black
(double black, pro line); 1 a 7 jsou přístupové cesty (access), 11 (lanovky) se vynechá.

Licence: data Trailforks jen pro nekomerční použití s atribucí – web ji ukazuje.

  TRAILFORKS_APP_ID=… TRAILFORKS_APP_SECRET=… python3 scripts/trailforks_trails.py --merge web/data/trails.geojson
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import PARKS, haversine_m, simplify  # noqa: E402
from gpx_trails import merge  # noqa: E402

API = "https://www.trailforks.com/api/1/trails"
ROWS = 200
DIFFICULTY = {1: "access", 2: "green", 3: "green", 4: "blue", 5: "red", 6: "black", 7: "access", 8: "black"}
SKIP = {11}  # lanovky / vleky kreslené jako „trail“


def decode_polyline(s: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google encoded polyline → [(lat, lon), …] (Trailforks track.encodedPath)."""
    coords, index, lat, lon, factor = [], 0, 0, 0, 10 ** precision
    while index < len(s):
        for which in (0, 1):
            result, shift = 0, 0
            while True:
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def trail_feature(trail: dict, park_id: str) -> dict | None:
    """Jedna lajna z odpovědi API → GeoJSON Feature, nebo None (bez geometrie / lanovka)."""
    try:
        diff_id = int(trail.get("difficulty") or 0)
    except (TypeError, ValueError):
        diff_id = 0
    if diff_id in SKIP:
        return None
    track = trail.get("track") or {}
    coords: list[tuple[float, float]] = []
    if track.get("encodedPath"):
        coords = decode_polyline(track["encodedPath"])
    elif isinstance(track.get("path"), list):
        coords = [(float(p[0]), float(p[1])) for p in track["path"] if len(p) >= 2]
    if len(coords) < 2:
        return None
    diff = DIFFICULTY.get(diff_id)
    length = sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))
    return {
        "type": "Feature",
        "geometry": {"type": "LineString",
                     "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in simplify(coords)]},
        "properties": {"park_id": park_id, "name": trail.get("title") or None,
                       "difficulty": None if diff in (None, "access") else diff, "access": diff == "access",
                       "length_m": round(length), "source": "trailforks", "trailforks_id": trail.get("trailid")},
    }


def fetch_region(rid: int, app_id: str, app_secret: str, timeout: int = 60) -> list[dict]:
    """Všechny lajny regionu (stránkuje po ROWS)."""
    trails, page = [], 1
    while True:
        q = urllib.parse.urlencode({"app_id": app_id, "app_secret": app_secret, "scope": "full",
                                    "filter": f"rid::{rid}", "rows": ROWS, "page": page})
        with urllib.request.urlopen(f"{API}?{q}", timeout=timeout) as r:
            data = json.load(r)
        if data.get("error"):
            raise RuntimeError(f"Trailforks rid {rid}: {data.get('message') or data['error']}")
        batch = data.get("data") or []
        trails.extend(batch)
        if len(batch) < ROWS:
            return trails
        page += 1


def collect(parks: list[dict], app_id: str, app_secret: str, cache: Path | None = None) -> list[dict]:
    cached = json.loads(cache.read_text()) if cache and cache.exists() else {}
    feats = []
    for f in parks:
        rid = f["properties"].get("trailforks_rid")
        pid = f["properties"]["id"]
        if not rid:
            continue
        try:
            raw = fetch_region(int(rid), app_id, app_secret)
            cached[str(rid)] = raw
        except Exception as e:  # noqa: BLE001 – síť / API; použije se poslední dobrá odpověď
            raw = cached.get(str(rid))
            print(f"trailforks: {pid} (rid {rid}): {e}" + (" – beru z cache" if raw else ""), file=sys.stderr)
            if not raw:
                continue
        got = [t for t in (trail_feature(t, pid) for t in raw) if t]
        print(f"trailforks: {pid}: {len(got)} lajn", file=sys.stderr)
        feats.extend(got)
    if cache:
        cache.write_text(json.dumps(cached, ensure_ascii=False, separators=(",", ":")))
    return feats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--merge", type=Path, help="trails.geojson, do kterého se lajny připojí")
    ap.add_argument("--out", type=Path, help="jen lajny z Trailforks jako GeoJSON")
    ap.add_argument("--cache", type=Path, help="JSON s poslední odpovědí API na region (při výpadku)")
    args = ap.parse_args(argv)

    app_id, app_secret = os.environ.get("TRAILFORKS_APP_ID"), os.environ.get("TRAILFORKS_APP_SECRET")
    parks = json.loads(args.parks.read_text())["features"]
    with_rid = [f["properties"]["id"] for f in parks if f["properties"].get("trailforks_rid")]
    if not with_rid:
        print("trailforks: žádný park nemá trailforks_rid, nic ke stažení", file=sys.stderr)
        return 0
    if not (app_id and app_secret):
        print(f"trailforks: chybí TRAILFORKS_APP_ID / TRAILFORKS_APP_SECRET, přeskakuji {len(with_rid)} parků", file=sys.stderr)
        return 0

    feats = collect(parks, app_id, app_secret, args.cache)
    if args.out:
        args.out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.merge:
        base = json.loads(args.merge.read_text()) if args.merge.exists() else \
            {"type": "FeatureCollection", "attribution": "© přispěvatelé OpenStreetMap, ODbL", "features": []}
        args.merge.write_text(json.dumps(merge(base, feats, replace_key="trailforks_id"), ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
