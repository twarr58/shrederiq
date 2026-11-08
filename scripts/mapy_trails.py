#!/usr/bin/env python3
"""Doplní do trails.geojson lajny, které v OSM nejsou, ale Mapy.com je mají (data/mapy/*.json).

Mapy.com vektorová data nedávají, ale geokódování zná lajny podle jména („Trail Easy Ella“,
kategorie MTB trail: poloha začátku a obdélník) a routing (bike_mountain) po jejich síti
jezdí – a výsledek routingu smí být zobrazený. Pro každou lajnu ze souboru parku:
  1. začátek = "start", jinak poloha z geokódování; konec = "end", jinak roh obdélníku
     nejdál od začátku,
  2. trasa routingem přes průjezdní body "via" (ručně ověřené na podkladu Mapy.com tam,
     kde by routing jel po cestě vedle lajny),
  3. lajna se připojí s vlastností "source": "mapy"; barvu a jméno doplní official_check.

Odpovědi se drží v cache (--cache), takže se API volá jen pro nové nebo změněné lajny.
Klíč: MAPY_SERVER_KEY (serverový, s Geocoding a Routing). Bez klíče a bez cache nic nemění.

  python3 scripts/mapy_trails.py web/data/trails.geojson --cache .osm-cache/mapy_trails.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import PARKS, haversine_m  # noqa: E402

MAPY_DIR = Path(__file__).resolve().parent.parent / "data" / "mapy"
API = "https://api.mapy.com/v1"
SEARCH_KM = 8  # geokódovaná lajna musí být nejvýš tak daleko od parku

Get = Callable[[str, dict], dict]


def mapy_get(api_key: str) -> Get:
    def get(path: str, params: dict) -> dict:
        req = urllib.request.Request(f"{API}/{path}?{urllib.parse.urlencode(params)}",
                                     headers={"X-Mapy-Api-Key": api_key, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.load(res)
    return get


def _dist(a, b) -> float:
    return haversine_m(a[1], a[0], b[1], b[0])


def locate(name: str, query: str, park: tuple[float, float], get: Get) -> dict | None:
    """Geokódovaná MTB lajna u parku: {"start": (lon, lat), "bbox": [x0, y0, x1, y1]}."""
    d = get("geocode", {"query": query, "lang": "cs", "limit": 5,
                        "preferNear": f"{park[0]},{park[1]}", "preferNearPrecision": 3000})
    for it in d.get("items", []):
        pos = (it["position"]["lon"], it["position"]["lat"])
        if it.get("label") == "MTB trail" and _dist(pos, park) <= SEARCH_KM * 1000:
            return {"start": pos, "bbox": it["bbox"]}
    return None


def build_trail(spec: dict, park: tuple[float, float], get: Get) -> list[list[float]] | None:
    start, end = spec.get("start"), spec.get("end")
    if not start or not end:
        found = locate(spec["name"], spec.get("query") or f"Trail {spec['name']}", park, get)
        if not found:
            return None
        start = start or found["start"]
        x0, y0, x1, y1 = found["bbox"]
        end = end or max([(x0, y0), (x0, y1), (x1, y0), (x1, y1)], key=lambda c: _dist(c, start))
    params = {"start": f"{start[0]},{start[1]}", "end": f"{end[0]},{end[1]}", "routeType": "bike_mountain", "format": "geojson"}
    if spec.get("via"):
        params["waypoints"] = ";".join(f"{x},{y}" for x, y in spec["via"])
    res = get("routing/route", params)
    coords = res["geometry"]["geometry"]["coordinates"]
    return [[round(x, 6), round(y, 6)] for x, y in coords]


def length_m(coords) -> float:
    return sum(_dist(a, b) for a, b in zip(coords, coords[1:]))


def add_trails(fc: dict, parks: dict[str, tuple[float, float]], cache: dict, get: Get | None,
               mapy_dir: Path = MAPY_DIR) -> tuple[int, int]:
    """Vrací (přidáno lajn, nenalezeno)."""
    added = missing = 0
    for path in sorted(mapy_dir.glob("*.json")):
        pid = path.stem
        if pid not in parks:
            continue
        for spec in json.loads(path.read_text()).get("trails", []):
            key = f"{pid}:" + json.dumps(spec, sort_keys=True, ensure_ascii=False)
            coords = cache.get(key)
            if coords is None and get is not None:
                try:
                    coords = build_trail(spec, parks[pid], get)
                except Exception as e:  # noqa: BLE001 – jedna lajna nesmí shodit build
                    print(f"mapy: {pid} {spec['name']}: {e}", file=sys.stderr)
                    coords = None
                if coords:
                    cache[key] = coords
            if not coords:
                missing += 1
                print(f"mapy: {pid} {spec['name']}: nenalezeno", file=sys.stderr)
                continue
            fc["features"].append({
                "type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"park_id": pid, "name": spec["name"], "difficulty": None, "access": False,
                               "length_m": round(length_m(coords)), "source": "mapy", "osm": f"mapy:{spec['name']}"}})
            added += 1
    return added, missing


def apply_renames(fc: dict, mapy_dir: Path = MAPY_DIR) -> int:
    """Přejmenuje lajny z OSM podle "rename" v data/mapy/<park>.json ({"jméno v OSM" nebo
    "way/<id>": "jméno na Mapy.com"}); původní jméno zůstane v osm_name. Oficiální seznam (official_check) má
    přednost – běží až potom. Vrací počet přejmenovaných úseků."""
    renames = {}
    for path in sorted(mapy_dir.glob("*.json")):
        for old, new in json.loads(path.read_text()).get("rename", {}).items():
            renames[(path.stem, old)] = new
    n = 0
    for f in fc.get("features", []):
        p = f["properties"]
        new = renames.get((p.get("park_id"), p.get("name")))
        # klíč může být i id cesty v OSM ("way/123") – pro lajny, které v OSM nemají jméno
        for way in (p.get("osm") or "").split("+"):
            new = new or renames.get((p.get("park_id"), way))
        if new and not p.get("access"):
            p["osm_name"], p["name"] = p.get("name"), new
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path, help="trails.geojson (doplní se)")
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--cache", type=Path)
    args = ap.parse_args(argv)
    key = os.environ.get("MAPY_SERVER_KEY") or os.environ.get("MAPY_ELEVATION_KEY")
    cache = json.loads(args.cache.read_text()) if args.cache and args.cache.exists() else {}
    if not key and not cache:
        print("mapy: bez MAPY_SERVER_KEY a bez cache, lajny z Mapy.com se nepřidají (jen přejmenování)", file=sys.stderr)
    parks = {f["properties"]["id"]: tuple(f["geometry"]["coordinates"])
             for f in json.loads(args.parks.read_text())["features"]}
    fc = json.loads(args.trails.read_text())
    renamed = apply_renames(fc)
    added, missing = add_trails(fc, parks, cache, mapy_get(key) if key else None)
    args.trails.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    if args.cache:
        args.cache.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))
    print(f"mapy: přidáno {added} lajn z Mapy.com, nenalezeno {missing}, přejmenováno {renamed} úseků", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
