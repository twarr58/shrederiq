#!/usr/bin/env python3
"""Doplní do web/data/trails.geojson výšky a sklon lajn z Mapy.com Elevation API.

Pro každou lajnu:
  1. převzorkuje geometrii po ~STEP_M metrech (jen pro dotazy na výšky – geometrie lajny
     zůstává původní, výšky jejích bodů se dopočítají lineárně podle vzdálenosti podél lajny;
     dřív se geometrie přepisovala na body po 25 m a zatáčky se tím usekly),
  2. zjistí výšky bodů (Mapy.com /v1/elevation, až 256 bodů na dotaz; výsledky se
     ukládají do cache podle souřadnic zaokrouhlených na 5 desetinných míst),
  3. spočítá sklon každého úseku geometrie přes okno ~WINDOW_M metrů (DEM je mezi
     sousedními body zašuměný) a uloží:
       slopes     – sklon v % pro každý úsek (znaménko podle směru geometrie,
                    který v OSM nemusí odpovídat směru jízdy),
       eles       – výška každého bodu geometrie v m (výškový profil),
       ele_min, ele_max, slope_avg (|konec − začátek| / délka), slope_max (max |sklon|).

Klíč: proměnná prostředí MAPY_SERVER_KEY (serverový klíč Mapy.com s Elevation + Routing),
jinak MAPY_ELEVATION_KEY. Klíč stránky MAPY_API_KEY se nepoužívá: je omezený na doménu
a z Actions vrací 403. Bez klíče skript
soubor nezmění (exit 0). Při chybě API skončí s chybou a soubor nezmění.

Příklad:
  MAPY_ELEVATION_KEY=… python3 scripts/elevation.py web/data/trails.geojson --cache .osm-cache/elevation.json
"""
from __future__ import annotations

import argparse
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

API = "https://api.mapy.com/v1/elevation"
BATCH = 256
STEP_M = 25
WINDOW_M = 75

Point = tuple[float, float]  # (lon, lat)


def haversine_m(a: Point, b: Point) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def key_of(p: Point) -> str:
    return f"{p[0]:.5f},{p[1]:.5f}"


def resample(coords: list[Point], step_m: float = STEP_M) -> list[Point]:
    """Body po step_m metrech podél lomené čáry (první a poslední bod zůstanou)."""
    out = [coords[0]]
    since = 0.0  # vzdálenost od posledního vloženého bodu k začátku úseku
    for a, b in zip(coords, coords[1:]):
        seg = haversine_m(a, b)
        pos = step_m - since  # kde na úseku leží další bod
        while pos <= seg:
            t = pos / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            pos += step_m
        since = seg - (pos - step_m)
    if haversine_m(out[-1], coords[-1]) > step_m / 5:
        out.append(coords[-1])
    else:
        out[-1] = coords[-1]
    return [(round(lon, 5), round(lat, 5)) for lon, lat in out]


def segment_slopes(points: list[Point], eles: list[float], window_m: float = WINDOW_M) -> list[int]:
    """Sklon (%) každého úseku points[i]→points[i+1], počítaný přes okno ~window_m kolem úseku."""
    n = len(points)
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_m(a, b))
    out = []
    for i in range(n - 1):
        j0, j1 = i, i + 1
        while cum[j1] - cum[j0] < window_m and (j0 > 0 or j1 < n - 1):
            if j0 > 0:
                j0 -= 1
            if j1 < n - 1 and cum[j1] - cum[j0] < window_m:
                j1 += 1
        dist = cum[j1] - cum[j0]
        out.append(round((eles[j1] - eles[j0]) / dist * 100) if dist > 0 else 0)
    return out


def summarize(points: list[Point], eles: list[float], slopes: list[int]) -> dict:
    length = sum(haversine_m(a, b) for a, b in zip(points, points[1:]))
    return {
        "ele_min": round(min(eles)),
        "ele_max": round(max(eles)),
        "slope_avg": round(abs(eles[-1] - eles[0]) / length * 100, 1) if length else 0,
        "slope_max": max((abs(s) for s in slopes), default=0),
    }


# --- Mapy.com Elevation API --------------------------------------------------

def mapy_fetch(api_key: str) -> Callable[[list[Point]], list[float]]:
    def fetch(points: list[Point]) -> list[float]:
        q = urllib.parse.urlencode([("lang", "cs")] + [("positions", key_of(p)) for p in points])
        req = urllib.request.Request(f"{API}?{q}", headers={"X-Mapy-Api-Key": api_key, "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    data = json.load(res)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 * 2 ** attempt)
                    continue
                raise
        items = data.get("items") or []
        if len(items) != len(points):
            raise RuntimeError(f"elevation API: čekal jsem {len(points)} výšek, přišlo {len(items)}")
        return [float(it["elevation"]) for it in items]
    return fetch


def lookup_elevations(points: list[Point], cache: dict[str, float], fetch: Callable[[list[Point]], list[float]],
                      batch: int = BATCH) -> int:
    """Doplní do cache výšky chybějících bodů. Vrací počet dotazů na API."""
    missing = list(dict.fromkeys(key_of(p) for p in points if key_of(p) not in cache))
    calls = 0
    for i in range(0, len(missing), batch):
        chunk = missing[i:i + batch]
        pts = [tuple(map(float, k.split(","))) for k in chunk]
        for k, e in zip(chunk, fetch(pts)):
            cache[k] = round(e, 1)
        calls += 1
    return calls


def along(coords: list[Point]) -> list[float]:
    """Vzdálenost každého bodu od začátku lomené čáry (m)."""
    out = [0.0]
    for a, b in zip(coords, coords[1:]):
        out.append(out[-1] + haversine_m(a, b))
    return out


def interpolate(at: list[float], xs: list[float], ys: list[float]) -> list[float]:
    """Lineární interpolace ys(xs) v bodech at (xs rostoucí)."""
    out, j = [], 0
    for x in at:
        while j < len(xs) - 2 and xs[j + 1] < x:
            j += 1
        x0, x1 = xs[j], xs[min(j + 1, len(xs) - 1)]
        y0, y1 = ys[j], ys[min(j + 1, len(ys) - 1)]
        t = 0.0 if x1 == x0 else min(1.0, max(0.0, (x - x0) / (x1 - x0)))
        out.append(y0 + (y1 - y0) * t)
    return out


def enrich(fc: dict, cache: dict[str, float], fetch: Callable[[list[Point]], list[float]]) -> int:
    """Doplní výšky a sklon; geometrie lajn se nemění. Vrací počet dotazů na API."""
    resampled = []
    for f in fc["features"]:
        pts = resample([tuple(c[:2]) for c in f["geometry"]["coordinates"]])
        resampled.append(pts)
    calls = lookup_elevations([p for pts in resampled for p in pts], cache, fetch)
    for f, pts in zip(fc["features"], resampled):
        coords = [tuple(c[:2]) for c in f["geometry"]["coordinates"]]
        dist = along(coords)
        # resample() klade body po STEP_M podél lajny, poslední bod je konec lajny
        at = [min(i * STEP_M, dist[-1]) for i in range(len(pts))]
        at[-1] = dist[-1]
        eles = interpolate(dist, at, [cache[key_of(p)] for p in pts])
        slopes = segment_slopes(coords, eles)
        # eles = výška každého bodu lajny (m) pro výškový profil ve webu
        f["properties"].update(slopes=slopes, eles=[round(e) for e in eles], **summarize(coords, eles, slopes))
    return calls


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path, help="trails.geojson (přepíše se)")
    ap.add_argument("--cache", type=Path, help="JSON cache výšek {\"lon,lat\": výška}")
    args = ap.parse_args(argv)

    api_key = os.environ.get("MAPY_SERVER_KEY") or os.environ.get("MAPY_ELEVATION_KEY")
    if not api_key:
        print("elevation: bez MAPY_SERVER_KEY, sklon se nepočítá", file=sys.stderr)
        return 0

    fc = json.loads(args.trails.read_text())
    cache = json.loads(args.cache.read_text()) if args.cache and args.cache.exists() else {}
    try:
        calls = enrich(fc, cache, mapy_fetch(api_key))
    finally:
        if args.cache:  # i částečně stažené výšky se hodí příště
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(json.dumps(cache, separators=(",", ":")))
    args.trails.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n")
    pts = sum(len(f["geometry"]["coordinates"]) for f in fc["features"])
    print(f"elevation: {len(fc['features'])} lajn, {pts} bodů, {calls} dotazů na API, cache {len(cache)} bodů", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
