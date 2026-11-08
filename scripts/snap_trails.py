#!/usr/bin/env python3
"""Přichytí lajny z OSM k síti Mapy.com přes routing API (profil bike_mountain), #22.

OSM má lajny bikeparků často nakreslené hrubě, Mapy.com přesně, ale vektorová data
nedávají. Routing po jejich síti ale smí být zobrazený. Pro každou lajnu:
  1. z geometrie OSM se vezme start, cíl a průjezdní body po ~WAYPOINT_M (nejvýš
     MAX_WAYPOINTS, jinak se rovnoměrně proředí),
  2. /v1/routing/route?routeType=bike_mountain vrátí trasu po síti Mapy.com,
  3. když se trasa od OSM neodchýlí o víc než MAX_DEV_M (oboustranně) a není o víc než
     MAX_LONGER delší, nahradí geometrii lajny (vlastnost "snapped": true); hrubě
     nakreslenou lajnu (serpentiny jako tětivy) nahradí i s odchylkou do LOOSE_DEV_M,
     když medián odchylky je do LOOSE_MEDIAN_M a trasa podél lajny necouvá;
     jinak zůstane OSM a lajna dostane "snap_dev" (metry) pro ruční kontrolu.
Odpovědi se drží v cache podle id cesty a otisku souřadnic, takže se přepočítají jen
změněné cesty. Lesní / přístupové cesty a lajny z GPX / Trailforks se nepřichytávají.

Klíč: MAPY_SERVER_KEY, jinak MAPY_ELEVATION_KEY (serverový klíč
bez omezení domény se službou Routing). Bez klíče skript nic nemění (exit 0).

  MAPY_SERVER_KEY=… python3 scripts/snap_trails.py web/data/trails.geojson --cache .osm-cache/snap.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevation import Point, haversine_m, resample  # noqa: E402

API = "https://api.mapy.com/v1/routing/route"
WAYPOINT_M = 100
MAX_WAYPOINTS = 15
MAX_DEV_M = 25
MAX_LONGER = 1.5
# OSM má lajnu hrubě (serpentiny jako tětivy): trasa po síti Mapy.com se ve špičkách
# zatáček odchýlí víc než MAX_DEV_M, i když jede po té samé lajně. Pak se přijme, když
# většinou sedí (medián) a podél lajny z OSM jen postupuje (žádná odbočka tam a zpět).
LOOSE_DEV_M = 50
LOOSE_MEDIAN_M = 8
BACKTRACK_M = 15
MIN_LENGTH_M = 60


def _dist_to_segment(p: Point, a: Point, b: Point) -> float:
    k = math.cos(math.radians(a[1])) * 111_320
    px, py = (p[0] - a[0]) * k, (p[1] - a[1]) * 111_320
    bx, by = (b[0] - a[0]) * k, (b[1] - a[1]) * 111_320
    dd = bx * bx + by * by
    t = 0.0 if dd == 0 else max(0.0, min(1.0, (px * bx + py * by) / dd))
    return math.hypot(px - t * bx, py - t * by)


def _dist_to_line(p: Point, line: list[Point]) -> float:
    if len(line) == 1:
        return haversine_m(p, line[0])
    return min(_dist_to_segment(p, a, b) for a, b in zip(line, line[1:]))


def deviation_m(a: list[Point], b: list[Point]) -> float:
    """Oboustranná největší odchylka dvou lomených čar (Hausdorff po vrcholech)."""
    return max(max(_dist_to_line(p, b) for p in a), max(_dist_to_line(p, a) for p in b))


def median_dev_m(a: list[Point], b: list[Point]) -> float:
    """Medián vzdáleností vrcholů obou čar od té druhé."""
    d = sorted([_dist_to_line(p, b) for p in a] + [_dist_to_line(p, a) for p in b])
    return d[len(d) // 2]


def _along(p: Point, line: list[Point]) -> float:
    """Vzdálenost podél line k bodu line nejbližšímu k p."""
    best, best_d, run = 0.0, float("inf"), 0.0
    for a, b in zip(line, line[1:]):
        seg = haversine_m(a, b)
        d = _dist_to_segment(p, a, b)
        if d < best_d:
            da, db = haversine_m(p, a), haversine_m(p, b)
            t = 0.0 if seg == 0 else max(0.0, min(1.0, (da * da - db * db + seg * seg) / (2 * seg * seg)))
            best, best_d = run + t * seg, d
        run += seg
    return best


def backtracks(route: list[Point], line: list[Point], tol_m: float = BACKTRACK_M) -> bool:
    """Couvá trasa podél lajny o víc než tol_m (odbočka tam a zpět, jiná cesta)?"""
    top = 0.0
    for p in route:
        s = _along(p, line)
        if s < top - tol_m:
            return True
        top = max(top, s)
    return False


def accept(coords: list[Point], snapped: list[Point]) -> tuple[bool, float]:
    """Nahradit OSM trasou z Mapy.com? Vrací (ano/ne, největší odchylka)."""
    dev = deviation_m(coords, snapped)
    if length_m(snapped) > MAX_LONGER * length_m(coords):
        return False, dev
    if dev <= MAX_DEV_M:
        return True, dev
    ok = dev <= LOOSE_DEV_M and median_dev_m(coords, snapped) <= LOOSE_MEDIAN_M and not backtracks(snapped, coords)
    return ok, dev


def length_m(line: list[Point]) -> float:
    return sum(haversine_m(p, q) for p, q in zip(line, line[1:]))


def waypoints(coords: list[Point]) -> list[Point]:
    """Průjezdní body mezi startem a cílem: po WAYPOINT_M, nejvýš MAX_WAYPOINTS."""
    inner = resample(coords, WAYPOINT_M)[1:-1]
    if len(inner) > MAX_WAYPOINTS:
        step = len(inner) / MAX_WAYPOINTS
        inner = [inner[int(i * step)] for i in range(MAX_WAYPOINTS)]
    return inner


def fingerprint(coords: list[Point]) -> str:
    return hashlib.sha1(json.dumps([[round(x, 6), round(y, 6)] for x, y in coords]).encode()).hexdigest()[:16]


def mapy_route(api_key: str) -> Callable[[Point, Point, list[Point]], dict]:
    def route(start: Point, end: Point, via: list[Point]) -> dict:
        params = [("start", f"{start[0]:.6f},{start[1]:.6f}"), ("end", f"{end[0]:.6f},{end[1]:.6f}"),
                  ("routeType", "bike_mountain"), ("format", "geojson"), ("lang", "cs")]
        params += [("waypoints", f"{p[0]:.6f},{p[1]:.6f}") for p in via]
        req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                     headers={"X-Mapy-Api-Key": api_key, "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    return json.load(res)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 * 2 ** attempt)
                    continue
                raise
        raise RuntimeError("routing: bez odpovědi")
    return route


def route_coords(res: dict) -> list[Point]:
    g = res.get("geometry") or {}
    if g.get("type") == "Feature":
        g = g.get("geometry") or {}
    if g.get("type") != "LineString":
        return []
    return [(float(c[0]), float(c[1])) for c in g["coordinates"]]


def snap_feature(f: dict, cache: dict, route: Callable[[Point, Point, list[Point]], dict]) -> str:
    """Vrátí 'snapped' / 'kept' / 'skipped'. Upravuje f na místě."""
    p = f["properties"]
    coords = [tuple(c[:2]) for c in f["geometry"]["coordinates"]]
    if p.get("access") or p.get("connector") or p.get("source") in ("gpx", "trailforks", "mapy") or length_m(coords) < MIN_LENGTH_M:
        return "skipped"
    key = f"{p.get('osm') or p.get('name')}:{fingerprint(coords)}"
    entry = cache.get(key)
    if entry is None:
        try:
            res = route(coords[0], coords[-1], waypoints(coords))
            entry = {"coords": [[round(x, 6), round(y, 6)] for x, y in route_coords(res)]}
        except Exception as e:  # noqa: BLE001 – jedna lajna nesmí shodit build
            entry = {"error": str(e)[:120]}
        cache[key] = entry
    snapped = [tuple(c) for c in entry.get("coords") or []]
    if len(snapped) < 2:
        p["snap_dev"] = None
        return "kept"
    ok, dev = accept(coords, snapped)
    if ok:
        f["geometry"]["coordinates"] = [list(c) for c in snapped]
        p["length_m"] = round(length_m(snapped))
        p["snapped"] = True
        p.pop("snap_dev", None)
        return "snapped"
    p["snap_dev"] = round(dev)
    return "kept"


def snap_all(fc: dict, cache: dict, route) -> dict[str, int]:
    counts = {"snapped": 0, "kept": 0, "skipped": 0}
    for f in fc["features"]:
        counts[snap_feature(f, cache, route)] += 1
    return counts


def report(fc: dict) -> str:
    """Lajny, které routing neumí sledovat (k ruční kontrole), po parcích."""
    rows = [(f["properties"]["park_id"], f["properties"].get("name") or "bez názvu", f["properties"]["snap_dev"])
            for f in fc["features"] if f["properties"].get("snap_dev")]
    if not rows:
        return "Všechny lajny sedí na síť Mapy.com do 25 m.\n"
    out = ["| park | lajna | odchylka od Mapy.com |", "|---|---|---:|"]
    out += [f"| `{pid}` | {name} | {dev} m |" for pid, name, dev in sorted(rows, key=lambda r: (r[0], -r[2]))]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path, help="trails.geojson (přepíše se)")
    ap.add_argument("--cache", type=Path, help="JSON cache odpovědí routingu")
    ap.add_argument("--report", type=Path, help="markdown se seznamem lajn k ruční kontrole")
    args = ap.parse_args(argv)

    api_key = os.environ.get("MAPY_SERVER_KEY") or os.environ.get("MAPY_ELEVATION_KEY")
    if not api_key:
        print("snap: bez MAPY_SERVER_KEY, lajny zůstávají podle OSM", file=sys.stderr)
        return 0
    fc = json.loads(args.trails.read_text())
    cache = json.loads(args.cache.read_text()) if args.cache and args.cache.exists() else {}
    try:
        counts = snap_all(fc, cache, mapy_route(api_key))
    finally:
        if args.cache:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(json.dumps(cache, separators=(",", ":")))
    args.trails.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.report:
        args.report.write_text(report(fc))
    errors = sum(1 for v in cache.values() if "error" in v)
    print(f"snap: {counts['snapped']} lajn podle Mapy.com, {counts['kept']} zůstává podle OSM, "
          f"{counts['skipped']} přeskočeno, {errors} chyb routingu v cache", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
