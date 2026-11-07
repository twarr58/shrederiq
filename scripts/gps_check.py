#!/usr/bin/env python3
"""Kontrola lajn proti veřejným GPS stopám OpenStreetMap (#22, krok 3).

Kde lidé skutečně jezdí, je vidět z nahraných GPS stop. Pro každý park se stáhnou
stopy z obdélníku kolem jeho lajn (OSM API /api/0.6/trackpoints, stránky po 5 000
bodech) a každá lajna dostane „pokrytí“: podíl bodů lajny (po 25 m), u kterých je do
TOL_M metrů aspoň jeden bod stopy. Nízké pokrytí znamená lajnu bez provozu, zavřenou,
novou, nebo špatně zakreslenou – ve všech případech stojí za ruční kontrolu.

Výsledek jde do trails.geojson (vlastnost gps_cov, 0–100) a do markdown reportu.
Stopy se drží v cache podle obdélníku, ať se API nevolá při každém běhu.

  python3 scripts/gps_check.py web/data/trails.geojson --cache .osm-cache/gps.json --report out/gps.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevation import Point, haversine_m, resample  # noqa: E402

API = "https://api.openstreetmap.org/api/0.6/trackpoints"
PAGE_POINTS = 5000
MAX_PAGES = 20          # 100 000 bodů na park stačí, větší parky jsou stejně plné stop
TOL_M = 15
PAD_DEG = 0.003         # ~300 m okolo lajn
LOW_COV = 30            # pod 30 % = k ruční kontrole
UA = "shrederiq/1.0 (+https://github.com/twarr58/shrederiq)"


def bbox_of(features: list[dict], pad: float = PAD_DEG) -> tuple[float, float, float, float]:
    xs = [c[0] for f in features for c in f["geometry"]["coordinates"]]
    ys = [c[1] for f in features for c in f["geometry"]["coordinates"]]
    return (round(min(xs) - pad, 4), round(min(ys) - pad, 4), round(max(xs) + pad, 4), round(max(ys) + pad, 4))


def parse_trackpoints(gpx_xml: str) -> list[Point]:
    """Body <trkpt lat lon> z GPX odpovědi (jmenný prostor i bez něj)."""
    root = ET.fromstring(gpx_xml)
    return [(float(el.get("lon")), float(el.get("lat"))) for el in root.iter() if el.tag.endswith("trkpt")]


def osm_fetch(timeout: int = 60) -> Callable[[tuple[float, float, float, float], int], str]:
    def fetch(bbox, page):
        url = f"{API}?bbox={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}&page={page}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    return res.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(3 * 2 ** attempt)
                    continue
                raise
        raise RuntimeError("trackpoints: bez odpovědi")
    return fetch


def load_traces(bbox, cache: dict, fetch) -> list[Point]:
    key = ",".join(str(v) for v in bbox)
    if key in cache:
        return [tuple(p) for p in cache[key]]
    pts: list[Point] = []
    for page in range(MAX_PAGES):
        got = parse_trackpoints(fetch(bbox, page))
        pts.extend(got)
        if len(got) < PAGE_POINTS:
            break
    cache[key] = [[round(x, 5), round(y, 5)] for x, y in pts]
    return pts


class Grid:
    """Mřížka ~TOL_M pro rychlé „je do TOL_M nějaký bod stopy?“."""

    def __init__(self, pts: list[Point], cell_m: float = TOL_M):
        self.dlat = cell_m / 111_320
        self.cells: dict[tuple[int, int], list[Point]] = defaultdict(list)
        for p in pts:
            self.cells[self._cell(p)].append(p)

    def _cell(self, p: Point) -> tuple[int, int]:
        return (int(p[0] * math.cos(math.radians(p[1])) / self.dlat), int(p[1] / self.dlat))

    def near(self, p: Point, tol_m: float = TOL_M) -> bool:
        cx, cy = self._cell(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in self.cells.get((cx + dx, cy + dy), ()):
                    if haversine_m(p, q) <= tol_m:
                        return True
        return False


def coverage(coords: list[Point], grid: Grid) -> int:
    pts = resample(coords, 25)
    hit = sum(1 for p in pts if grid.near(p))
    return round(100 * hit / len(pts))


def check(fc: dict, cache: dict, fetch) -> dict[str, dict]:
    """Doplní gps_cov do lajn; vrací souhrn po parcích."""
    by_park: dict[str, list[dict]] = defaultdict(list)
    for f in fc["features"]:
        if not f["properties"].get("access"):
            by_park[f["properties"]["park_id"]].append(f)
    summary = {}
    for pid, feats in sorted(by_park.items()):
        bbox = bbox_of(feats)
        try:
            traces = load_traces(bbox, cache, fetch)
        except Exception as e:  # noqa: BLE001 – park bez stop nesmí shodit report
            print(f"gps: {pid}: {e}", file=sys.stderr)
            summary[pid] = {"error": str(e)[:100], "trails": len(feats)}
            continue
        grid = Grid(traces)
        covs = []
        for f in feats:
            cov = coverage([tuple(c[:2]) for c in f["geometry"]["coordinates"]], grid)
            f["properties"]["gps_cov"] = cov
            covs.append(cov)
        covs.sort()
        low = [f for f in feats if f["properties"]["gps_cov"] < LOW_COV]
        summary[pid] = {"points": len(traces), "trails": len(feats), "median": covs[len(covs) // 2],
                        "low": sorted({f["properties"].get("name") or "bez názvu" for f in low})}
    return summary


def render(summary: dict[str, dict]) -> str:
    out = ["# Lajny × GPS stopy OSM", "",
           "| park | bodů stop | úseků | medián pokrytí | úseky pod 30 % |", "|---|---:|---:|---:|---|"]
    for pid, s in summary.items():
        if "error" in s:
            out.append(f"| `{pid}` | – | {s['trails']} | – | chyba: {s['error']} |")
            continue
        low = ", ".join(s["low"][:8]) + (" …" if len(s["low"]) > 8 else "")
        out.append(f"| `{pid}` | {s['points']:,} | {s['trails']} | {s['median']} % | {low or '–'} |")
    out += ["", f"Pokrytí = podíl bodů lajny (po 25 m), u kterých je do {TOL_M} m bod veřejné GPS stopy. "
                "Nízké pokrytí: bez provozu, zavřeno, nové, nebo špatně zakreslené.", "",
            "Stopy © přispěvatelé OpenStreetMap, ODbL."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path, help="trails.geojson (doplní se gps_cov)")
    ap.add_argument("--cache", type=Path, help="JSON cache stop podle obdélníku")
    ap.add_argument("--report", type=Path, help="markdown report")
    args = ap.parse_args(argv)
    fc = json.loads(args.trails.read_text())
    cache = json.loads(args.cache.read_text()) if args.cache and args.cache.exists() else {}
    try:
        summary = check(fc, cache, osm_fetch())
    finally:
        if args.cache:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(json.dumps(cache, separators=(",", ":")))
    args.trails.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n")
    text = render(summary)
    if args.report:
        args.report.write_text(text)
    print(text, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
