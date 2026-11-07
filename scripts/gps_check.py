#!/usr/bin/env python3
"""Kontrola lajn proti veřejným GPS stopám OpenStreetMap (#22, krok 3).

Kde lidé skutečně jezdí, je vidět z nahraných GPS stop. Pro každý park se stáhnou
stopy z obdélníku kolem jeho lajn (OSM API /api/0.6/trackpoints, stránky po 5 000
bodech) a každá lajna dostane „pokrytí“: podíl bodů lajny (po 25 m), u kterých je do
TOL_M metrů aspoň jeden bod stopy. Nízké pokrytí znamená lajnu bez provozu, zavřenou,
novou, nebo špatně zakreslenou – ve všech případech stojí za ruční kontrolu.

Výsledek jde do trails.geojson (vlastnost gps_cov, 0–100) a do markdown reportu.
S --fit se navazující úseky stejné lajny nejdřív spojí (OSM je seká na kousky) a lajny
s pokrytím pod 85 % se narovnají podle stop: každý bod po 25 m se posune do těžiště
bodů stop do 60 m (aspoň 5 bodů), čára se vyhladí; výsledek se vezme jen když má
pokrytí aspoň 70 %, o 10 bodů víc než předtím, a není o víc než 30 % delší
(vlastnost gps_fit).
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
MAX_PAGES = 8           # 40 000 bodů na park stačí, větší parky jsou stejně plné stop
WORKERS = 4             # parky se stahují paralelně (OSM API to snese, zdvořilý User-Agent)
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


FIT_R_M = 60          # jak daleko od bodu lajny hledat stopy při narovnávání
FIT_MIN_PTS = 5       # méně bodů stop v okruhu = nespolehlivé, bod se nehýbe (konce lajn mívají půl oblaku)
FIT_MIN_COV = 70      # narovnaná lajna musí mít aspoň takové pokrytí, jinak zůstane OSM
FIT_MIN_GAIN = 10     # a pokrytí musí stoupnout aspoň o tolik bodů
FIT_BELOW = 85        # narovnávají se lajny s pokrytím pod tuto hranici
FIT_MAX_LONGER = 1.3
JOIN_M = 8            # navazující úseky stejné lajny (konec–začátek do 8 m) se před hodnocením spojí
FIT_STEP_M = 10       # hustota bodů při narovnávání (po 25 m by serpentiny ztratily zatáčky)
FIT_MIN_LEN_M = 50    # kratší pahýly se nenarovnávají (pár bodů v oblaku stop je smrskne na nic)


class Grid:
    """Mřížka ~TOL_M pro rychlé „je do TOL_M nějaký bod stopy?“."""

    def __init__(self, pts: list[Point], cell_m: float = TOL_M):
        self.dlat = cell_m / 111_320
        self.cells: dict[tuple[int, int], list[Point]] = defaultdict(list)
        for p in pts:
            self.cells[self._cell(p)].append(p)

    def _cell(self, p: Point) -> tuple[int, int]:
        return (int(p[0] * math.cos(math.radians(p[1])) / self.dlat), int(p[1] / self.dlat))

    def within(self, p: Point, r_m: float) -> list[Point]:
        n = max(1, int(math.ceil(r_m / (self.dlat * 111_320))))
        cx, cy = self._cell(p)
        out = []
        for dx in range(-n, n + 1):
            for dy in range(-n, n + 1):
                out += [q for q in self.cells.get((cx + dx, cy + dy), ()) if haversine_m(p, q) <= r_m]
        return out

    def near(self, p: Point, tol_m: float = TOL_M) -> bool:
        cx, cy = self._cell(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in self.cells.get((cx + dx, cy + dy), ()):
                    if haversine_m(p, q) <= tol_m:
                        return True
        return False


def length(coords: list[Point]) -> float:
    return sum(haversine_m(a, b) for a, b in zip(coords, coords[1:]))


def coverage(coords: list[Point], grid: Grid) -> int:
    pts = resample(coords, 25)
    hit = sum(1 for p in pts if grid.near(p))
    return round(100 * hit / len(pts))


def fit_to_traces(coords: list[Point], grid: Grid) -> list[Point] | None:
    """Narovná lajnu podle stop: každý bod (po FIT_STEP_M) se posune do těžiště bodů stop do
    FIT_R_M, pokud jich je aspoň FIT_MIN_PTS; pak se čára vyhladí (průměr sousedů).
    Vrací None, když se nic nepohnulo, výsledek je o moc delší, nebo pokrytí nestačí."""
    pts = resample(coords, FIT_STEP_M)
    moved, out = 0, []
    for p in pts:
        cloud = grid.within(p, FIT_R_M)
        if len(cloud) >= FIT_MIN_PTS:
            out.append((sum(q[0] for q in cloud) / len(cloud), sum(q[1] for q in cloud) / len(cloud)))
            moved += 1
        else:
            out.append(p)
    if moved < max(3, len(pts) // 2):
        return None
    smooth = [out[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                         for a, b, c in zip(out, out[1:], out[2:])] + [out[-1]]
    if length(smooth) > FIT_MAX_LONGER * length(coords) or coverage(smooth, grid) < FIT_MIN_COV:
        return None
    return [(round(x, 6), round(y, 6)) for x, y in smooth]


def join_segments(feats: list[dict]) -> list[dict]:
    """Spojí navazující úseky stejné lajny (stejný park, název, obtížnost, zdroj) do jedné
    LineString: OSM lajny bývají rozsekané na kousky po pár metrech, které se samostatně
    nedají hodnotit ani narovnat. Pořadí a směr se dohledá podle koncových bodů."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in feats:
        p = f["properties"]
        key = (p.get("park_id"), p.get("name"), p.get("difficulty"), p.get("source"), bool(p.get("access")))
        groups[key].append(f)
    out = []
    for key, members in groups.items():
        if key[1] is None or len(members) == 1:
            out.extend(members)
            continue
        chains = [[tuple(c[:2]) for c in f["geometry"]["coordinates"]] for f in members]
        ids = [[f["properties"].get("osm")] for f in members]
        merged = True
        while merged and len(chains) > 1:
            merged = False
            for i in range(len(chains)):
                for j in range(len(chains)):
                    if i == j:
                        continue
                    a, b = chains[i], chains[j]
                    if haversine_m(a[-1], b[0]) <= JOIN_M:
                        chains[i] = a + b[1:]
                    elif haversine_m(a[-1], b[-1]) <= JOIN_M:
                        chains[i] = a + b[::-1][1:]
                    elif haversine_m(a[0], b[0]) <= JOIN_M:
                        chains[i] = b[::-1] + a[1:]
                    else:
                        continue
                    ids[i] += ids[j]
                    del chains[j], ids[j]
                    merged = True
                    break
                if merged:
                    break
        for chain, osm_ids in zip(chains, ids):
            f = json.loads(json.dumps(members[0]))
            f["geometry"]["coordinates"] = [list(c) for c in chain]
            f["properties"]["length_m"] = round(length(chain))
            f["properties"]["osm"] = "+".join(x for x in osm_ids if x)
            f["properties"]["segments"] = len(osm_ids)
            out.append(f)
    return out


def check(fc: dict, cache: dict, fetch, fit: bool = False) -> dict[str, dict]:
    """Doplní gps_cov do lajn; vrací souhrn po parcích."""
    if fit:  # navazující úseky spojit, ať se hodnotí a narovnávají celé lajny
        fc["features"] = join_segments(fc["features"])
    by_park: dict[str, list[dict]] = defaultdict(list)
    for f in fc["features"]:
        if not f["properties"].get("access"):
            by_park[f["properties"]["park_id"]].append(f)
    summary = {}
    # stopy všech parků najednou (sekvenčně to pro 32 parků trvalo 11 minut)
    from concurrent.futures import ThreadPoolExecutor
    boxes = {pid: bbox_of(feats) for pid, feats in by_park.items()}

    def fetch_park(pid):
        try:
            return pid, load_traces(boxes[pid], cache, fetch), None
        except Exception as e:  # noqa: BLE001 – park bez stop nesmí shodit report
            return pid, None, str(e)[:100]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        loaded = dict((pid, (traces, err)) for pid, traces, err in pool.map(fetch_park, sorted(by_park)))
    for pid, feats in sorted(by_park.items()):
        traces, err = loaded[pid]
        if err:
            print(f"gps: {pid}: {err}", file=sys.stderr)
            summary[pid] = {"error": err, "trails": len(feats)}
            continue
        grid = Grid(traces)
        covs, fitted = [], 0
        for f in feats:
            coords = [tuple(c[:2]) for c in f["geometry"]["coordinates"]]
            cov = coverage(coords, grid)
            if fit and cov < FIT_BELOW and length(coords) >= FIT_MIN_LEN_M and not f["properties"].get("snapped"):
                new = fit_to_traces(coords, grid)
                if new and coverage(new, grid) >= cov + FIT_MIN_GAIN:
                    f["geometry"]["coordinates"] = [list(c) for c in new]
                    f["properties"]["length_m"] = round(length(new))
                    f["properties"]["gps_fit"] = True
                    cov = coverage(new, grid)
                    fitted += 1
            f["properties"]["gps_cov"] = cov
            covs.append(cov)
        covs.sort()
        low = [f for f in feats if f["properties"]["gps_cov"] < LOW_COV]
        summary[pid] = {"points": len(traces), "trails": len(feats), "median": covs[len(covs) // 2], "fitted": fitted,
                        "low": sorted({f["properties"].get("name") or "bez názvu" for f in low})}
    return summary


def render(summary: dict[str, dict]) -> str:
    out = ["# Lajny × GPS stopy OSM", "",
           "| park | bodů stop | úseků | medián pokrytí | narovnáno | úseky pod 30 % |", "|---|---:|---:|---:|---:|---|"]
    for pid, s in summary.items():
        if "error" in s:
            out.append(f"| `{pid}` | – | {s['trails']} | – | – | chyba: {s['error']} |")
            continue
        low = ", ".join(s["low"][:8]) + (" …" if len(s["low"]) > 8 else "")
        out.append(f"| `{pid}` | {s['points']:,} | {s['trails']} | {s['median']} % | {s.get('fitted', 0)} | {low or '–'} |")
    out += ["", f"Pokrytí = podíl bodů lajny (po 25 m), u kterých je do {TOL_M} m bod veřejné GPS stopy. "
                "Nízké pokrytí: bez provozu, zavřeno, nové, nebo špatně zakreslené.", "",
            "Stopy © přispěvatelé OpenStreetMap, ODbL."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path, help="trails.geojson (doplní se gps_cov)")
    ap.add_argument("--cache", type=Path, help="JSON cache stop podle obdélníku")
    ap.add_argument("--report", type=Path, help="markdown report")
    ap.add_argument("--fit", action="store_true",
                    help="lajny s pokrytím pod 30 %% narovnat podle stop (těžiště stop do 40 m), když to pokrytí zvedne na 70 %%+")
    args = ap.parse_args(argv)
    fc = json.loads(args.trails.read_text())
    cache = json.loads(args.cache.read_text()) if args.cache and args.cache.exists() else {}
    try:
        summary = check(fc, cache, osm_fetch(), fit=args.fit)
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
