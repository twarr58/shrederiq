#!/usr/bin/env python3
"""Kontrola lajn proti veřejným GPS stopám OpenStreetMap (#22, krok 3; narovnávání #26).

Kde lidé skutečně jezdí, je vidět z nahraných GPS stop. Pro každý park se stáhnou
stopy z obdélníku kolem jeho lajn (OSM API /api/0.6/trackpoints, stránky po 5 000
bodech) a každá lajna dostane „pokrytí“: podíl bodů lajny (po 25 m), u kterých je do
TOL_M metrů aspoň jeden bod stopy. Nízké pokrytí znamená lajnu bez provozu, zavřenou,
novou, nebo špatně zakreslenou – ve všech případech stojí za ruční kontrolu.

Výsledek jde do trails.geojson (vlastnost gps_cov, 0–100) a do markdown reportu.
S --fit se navazující úseky stejné lajny nejdřív spojí (OSM je seká na kousky) a lajny
s pokrytím pod 85 % se narovnají podle stop. Každý bod lajny po 10 m se posune na
hřbet hustoty stop: z bodů stop v okně ±15 m podél lajny a ±60 m napříč se spočítá
hustota podle kolmé odchylky a bod se posune na nejbližší výrazný vrchol (sousední
lajna má vlastní vrchol a čáru k sobě nepřitáhne). Přednost mají stopy (trkseg), které
jedou podél aspoň poloviny lajny. Body, jejichž posun se od sousedů liší o víc než
20 m, zůstanou podle OSM; čára se lehce vyhladí. Výsledek se vezme, jen když pokrytí
stoupne aspoň o 10 bodů na 70 %+, délka zůstane v 0,8–1,3× OSM a medián posunu je do
40 m (vlastnosti gps_fit, gps_fit_shift_m).
Stopy se drží v cache podle obdélníku, ať se API nevolá při každém běhu.

  python3 scripts/gps_check.py web/data/trails.geojson --cache .osm-cache/gps.json --report out/gps.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
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
MIN_SEG_PTS = 5         # kratší stopy (trkseg) se zahodí, o průběhu nic neřeknou
M_PER_DEG = 111_320     # metrů na stupeň zeměpisné šířky
UA = "shrederiq/1.0 (+https://github.com/twarr58/shrederiq)"

Traces = tuple[list[list[Point]], list[Point]]  # (stopy po trkseg, anonymní body bez pořadí)


def bbox_of(features: list[dict], pad: float = PAD_DEG) -> tuple[float, float, float, float]:
    xs = [c[0] for f in features for c in f["geometry"]["coordinates"]]
    ys = [c[1] for f in features for c in f["geometry"]["coordinates"]]
    return (round(min(xs) - pad, 4), round(min(ys) - pad, 4), round(max(xs) + pad, 4), round(max(ys) + pad, 4))


def parse_trackpoints(gpx_xml: str) -> Traces:
    """Body <trkpt lat lon> z GPX odpovědi (jmenný prostor i bez něj), po <trkseg>.

    OSM vrací soukromé stopy jako anonymní trkseg bez <time>, body seřazené podle souřadnic –
    ty nejsou průběh jízdy, jdou do „pytle“. Stopy s časem zůstanou jako segmenty
    (kratší než MIN_SEG_PTS se zahodí)."""
    root = ET.fromstring(gpx_xml)
    segs: list[list[Point]] = []
    bag: list[Point] = []
    for seg in root.iter():
        if not seg.tag.endswith("trkseg"):
            continue
        pts, timed = [], False
        for el in seg:
            if el.tag.endswith("trkpt"):
                pts.append((float(el.get("lon")), float(el.get("lat"))))
                timed = timed or any(c.tag.endswith("time") for c in el)
        if not timed:
            bag.extend(pts)
        elif len(pts) >= MIN_SEG_PTS:
            segs.append(pts)
    return segs, bag


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


def _rounded(pts: list[Point]) -> list[list[float]]:
    return [[round(x, 5), round(y, 5)] for x, y in pts]


def from_cache(entry) -> Traces:
    """Záznam cache → stopy. Starý formát (plochý seznam bodů) = jeden anonymní pytel."""
    if isinstance(entry, dict):
        return [[tuple(p) for p in s] for s in entry.get("segs", [])], [tuple(p) for p in entry.get("bag", [])]
    return [], [tuple(p) for p in entry]


def load_traces(bbox, cache: dict, fetch) -> Traces:
    key = ",".join(str(v) for v in bbox)
    # starý plochý seznam (bez stop) se stáhne znovu, ať mají parky i rozlišení stop;
    # cache pages se dědí z běhu na běh a sama by se neobnovila
    if key in cache and isinstance(cache[key], dict):
        return from_cache(cache[key])
    segs: list[list[Point]] = []
    bag: list[Point] = []
    for page in range(MAX_PAGES):
        xml = fetch(bbox, page)
        got_segs, got_bag = parse_trackpoints(xml)
        segs += got_segs
        bag += got_bag
        if xml.count("<trkpt") < PAGE_POINTS:
            break
    cache[key] = {"segs": [_rounded(s) for s in segs], "bag": _rounded(bag)}
    return segs, bag


def n_points(traces: Traces) -> int:
    return sum(len(s) for s in traces[0]) + len(traces[1])


FIT_MIN_PTS = 5       # vrchol hustoty musí mít aspoň tolik bodů stop, jinak se bod nehýbe
FIT_MIN_COV = 70      # narovnaná lajna musí mít aspoň takové pokrytí, jinak zůstane OSM
FIT_MIN_GAIN = 10     # a pokrytí musí stoupnout aspoň o tolik bodů
FIT_BELOW = 85        # narovnávají se lajny s pokrytím pod tuto hranici
FIT_LEN_RANGE = (0.8, 1.3)  # narovnaná délka vůči OSM
FIT_MAX_SHIFT_M = 40  # medián posunu bodů; víc = spíš jiná lajna než oprava nákresu
JOIN_M = 8            # navazující úseky stejné lajny (konec–začátek do 8 m) se před hodnocením spojí
FIT_STEP_M = 10       # hustota bodů při narovnávání (po 25 m by serpentiny ztratily zatáčky)
FIT_MIN_LEN_M = 50    # kratší pahýly se nenarovnávají (pár bodů v oblaku stop je smrskne na nic)
RIDGE_ALONG_M = 15    # okno hřbetu podél lajny (±)
RIDGE_ACROSS_M = 60   # a napříč (±)
RIDGE_BIN_M = 3       # krok histogramu kolmých odchylek
RIDGE_SIGMA_M = 6     # šířka Gaussova jádra hustoty
RIDGE_BG = 2.0        # vrchol musí být aspoň 2× nad pozadím (průměr okna / okolní minima)
RIDGE_MAX_SHIFT_M = 30  # vrchol dál od OSM (okno ±60 m) jen určuje pozadí; v hustých parcích jsou lajny 20–40 m od sebe
RIDGE_BLOCK_PTS = 3   # bližší shluk aspoň 3 bodů zablokuje skok na vzdálenější vrchol
FOLLOW_M = 40         # stopa „jede podél lajny“, když je do 40 m …
FOLLOW_SHARE = 0.5    # … od aspoň poloviny bodů lajny
JUMP_M = 20           # posun lišící se od sousedů o víc = skok na jinou lajnu, bod zůstane podle OSM
JUMP_WIN = 3          # sousedé = posunuté body do 3 (30 m) na každou stranu
GAP_FILL = 3          # krátké díry a konce (bez vrcholu) se dopočítají z posunutých sousedů
LOOP_FIXES = 3        # kolikrát zkusit smyčky opravit vrácením bodů na OSM, pak „shape“
FRAME_PTS = 2         # směr lajny pro kolmici z bodů ±2 (±20 m), ať se kolmice v zatáčce nepřetáčí
BAG = -1              # id anonymních bodů v mřížce


def _local(p: Point, q: Point) -> tuple[float, float]:
    """q vůči p v metrech (rovinná aproximace, na desítky metrů přesná)."""
    return (q[0] - p[0]) * M_PER_DEG * math.cos(math.radians(p[1])), (q[1] - p[1]) * M_PER_DEG


class Grid:
    """Mřížka ~TOL_M bodů stop s id stopy (BAG pro anonymní body)."""

    def __init__(self, pts: list[Point], cell_m: float = TOL_M, sids: list[int] | None = None):
        self.cell_m = cell_m
        self.dlat = cell_m / M_PER_DEG
        self.cells: dict[tuple[int, int], list[tuple[float, float, int]]] = defaultdict(list)
        for i, p in enumerate(pts):
            self.cells[self._cell(p)].append((p[0], p[1], sids[i] if sids else BAG))

    @classmethod
    def of(cls, traces: Traces) -> "Grid":
        segs, bag = traces
        seen: set[Point] = set()
        pts, sids = [], []
        # stejné body víckrát (stopa nahraná dvakrát, stání na místě) by vrchol hustoty nafoukly
        for sid, seg in [(i, s) for i, s in enumerate(segs)] + [(BAG, bag)]:
            for p in seg:
                if p not in seen:
                    seen.add(p)
                    pts.append(p)
                    sids.append(sid)
        return cls(pts, sids=sids)

    def _cell(self, p: Point) -> tuple[int, int]:
        return (int(p[0] * math.cos(math.radians(p[1])) / self.dlat), int(p[1] / self.dlat))

    def box(self, p: Point, r_m: float):
        """Body stop (lon, lat, sid) z buněk do r_m kolem p (bez přesného filtru vzdálenosti)."""
        n = max(1, int(math.ceil(r_m / self.cell_m)))
        cx, cy = self._cell(p)
        for dx in range(-n, n + 1):
            for dy in range(-n, n + 1):
                yield from self.cells.get((cx + dx, cy + dy), ())

    def near(self, p: Point, tol_m: float = TOL_M) -> bool:
        return any(math.hypot(*_local(p, q)) <= tol_m for q in self.box(p, tol_m))


def length(coords: list[Point]) -> float:
    return sum(haversine_m(a, b) for a, b in zip(coords, coords[1:]))


def coverage(coords: list[Point], grid: Grid) -> int:
    pts = resample(coords, 25)
    hit = sum(1 for p in pts if grid.near(p))
    return round(100 * hit / len(pts))


def following_segments(coords: list[Point], grid: Grid) -> set[int] | None:
    """Id stop (trkseg), které jedou podél aspoň FOLLOW_SHARE lajny do FOLLOW_M; None když žádná."""
    pts = resample(coords, 25)
    counts: dict[int, int] = defaultdict(int)
    for p in pts:
        for sid in {q[2] for q in grid.box(p, FOLLOW_M) if q[2] != BAG and math.hypot(*_local(p, q)) <= FOLLOW_M}:
            counts[sid] += 1
    out = {sid for sid, c in counts.items() if c >= FOLLOW_SHARE * len(pts)}
    return out or None


def _kernel() -> list[float]:
    k = int(3 * RIDGE_SIGMA_M / RIDGE_BIN_M)
    return [math.exp(-((i * RIDGE_BIN_M) ** 2) / (2 * RIDGE_SIGMA_M ** 2)) for i in range(-k, k + 1)]


KERNEL = _kernel()


def ridge_offset(offsets: list[float]) -> float | None:
    """Kolmá odchylka vrcholu hustoty nejbližšího k 0 (původní lajně), nebo None.

    offsets: kolmé odchylky bodů stop v okně (m, ±RIDGE_ACROSS_M). Hustota = histogram po
    RIDGE_BIN_M vyhlazený Gaussovým jádrem; vrchol platí, když má v povodí (mezi okolními
    minimy) aspoň FIT_MIN_PTS bodů a je aspoň RIDGE_BG× nad pozadím. Pozadí = průměr okna,
    nebo vyšší z okolních minim, je-li menší: hustě projetá sousední lajna zvedne průměr,
    ale slabší vlastní stopu tím neschová. Když nejbližší vrchol (aspoň RIDGE_BLOCK_PTS bodů)
    neplatí, bod se nehýbe – vzdálenější vrchol bývá sousední lajna nebo cesta."""
    nb = 2 * int(RIDGE_ACROSS_M / RIDGE_BIN_M) + 1
    half = nb // 2
    hist = [0] * nb
    for v in offsets:
        j = round(v / RIDGE_BIN_M) + half
        if 0 <= j < nb:
            hist[j] += 1
    k = len(KERNEL) // 2
    dens = [sum(hist[j + i] * w for i, w in zip(range(-k, k + 1), KERNEL) if 0 <= j + i < nb) for j in range(nb)]
    mean = sum(dens) / nb
    peaks = []  # (vzdálenost od 0, bin, platí?)
    for j in range(nb):
        left = dens[j - 1] if j else -1.0
        right = dens[j + 1] if j + 1 < nb else -1.0
        if not (dens[j] > 0 and dens[j] >= left and dens[j] > right):
            continue
        lo, hi = j, j
        while lo > 0 and dens[lo - 1] < dens[lo]:
            lo -= 1
        while hi + 1 < nb and dens[hi + 1] < dens[hi]:
            hi += 1
        pts = sum(hist[lo:hi + 1])
        if pts >= RIDGE_BLOCK_PTS:
            ok = pts >= FIT_MIN_PTS and dens[j] >= RIDGE_BG * min(mean, max(dens[lo], dens[hi]))
            peaks.append((abs(j - half), j, ok))
    peaks = [pk for pk in peaks if pk[0] * RIDGE_BIN_M <= RIDGE_MAX_SHIFT_M]
    if not peaks or not min(peaks)[2]:
        return None  # nejbližší stopa je slabá: radši nechat OSM než skočit na vzdálenější lajnu
    c = (min(peaks)[1] - half) * RIDGE_BIN_M
    near = [v for v in offsets if abs(v - c) <= RIDGE_SIGMA_M]
    return sum(near) / len(near) if near else c


def _frame(pts: list[Point], i: int, k: int = FRAME_PTS) -> tuple[float, float]:
    """Jednotkový směr lajny v bodě i (v metrech, z bodu i−k do i+k)."""
    a, b = pts[max(0, i - k)], pts[min(len(pts) - 1, i + k)]
    dx, dy = _local(a, b)
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d


def window_offsets(p: Point, t: tuple[float, float], grid: Grid, prefer: set[int] | None) -> list[float]:
    """Kolmé odchylky (m, kladně vlevo) bodů stop v okně kolem p; s prefer jen z těch stop."""
    out = []
    for q in grid.box(p, math.hypot(RIDGE_ALONG_M, RIDGE_ACROSS_M)):
        if prefer is not None and q[2] not in prefer:
            continue
        dx, dy = _local(p, q)
        if abs(dx * t[0] + dy * t[1]) <= RIDGE_ALONG_M:
            v = dy * t[0] - dx * t[1]
            if abs(v) <= RIDGE_ACROSS_M:
                out.append(v)
    return out


def ridge_shifts(pts: list[Point], grid: Grid, prefer: set[int] | None = None) -> list[float | None]:
    """Kolmý posun každého bodu na hřbet stop (None = bez platného vrcholu). S prefer jen ze
    stop, které jedou podél lajny (ostatní body bývají sousední lajny a cesty), jinak ze všech."""
    return [ridge_offset(window_offsets(p, _frame(pts, i), grid, prefer)) for i, p in enumerate(pts)]


def reject_jumps(shifts: list[float | None], jump_m: float = JUMP_M, win: int = JUMP_WIN) -> list[float | None]:
    """Posun, který se od posunu sousedů (medián posunutých bodů do `win` na každou stranu)
    liší o víc než jump_m, se zahodí (bod zůstane podle OSM) – typicky skok na sousední lajnu.
    Medián místo nejbližšího souseda: krátká série skoků (2–3 body) se tím vyřadí celá."""
    out = list(shifts)
    for i, s in enumerate(shifts):
        if s is None:
            continue
        nbrs = [shifts[j] for j in range(max(0, i - win), min(len(shifts), i + win + 1))
                if j != i and shifts[j] is not None]
        if nbrs and abs(s - statistics.median(nbrs)) > jump_m:
            out[i] = None
    return out


def fill_gaps(shifts: list[float | None], gap: int = GAP_FILL) -> list[float]:
    """Krátké díry mezi posunutými body se dopočítají lineárně a krátké konce převezmou posun
    nejbližšího bodu (na konci lajny je okno stop jen poloviční). Delší díry zůstanou podle
    OSM (0), jen se do nich posun během `gap` bodů plynule sníží – bez schodu přes celou šířku."""
    out = [s if s is not None else 0.0 for s in shifts]
    idx = [i for i, s in enumerate(shifts) if s is not None]
    if not idx:
        return out
    n = len(shifts)
    for a, b in zip(idx, idx[1:]):
        if 1 < b - a <= gap + 1:
            for i in range(a + 1, b):
                out[i] = shifts[a] + (shifts[b] - shifts[a]) * (i - a) / (b - a)
        elif b - a > gap + 1:
            _taper(out, a, +1, shifts[a], gap)
            _taper(out, b, -1, shifts[b], gap)
    if idx[0] <= gap:
        out[:idx[0]] = [shifts[idx[0]]] * idx[0]
    else:
        _taper(out, idx[0], -1, shifts[idx[0]], gap)
    if n - 1 - idx[-1] <= gap:
        out[idx[-1] + 1:] = [shifts[idx[-1]]] * (n - 1 - idx[-1])
    else:
        _taper(out, idx[-1], +1, shifts[idx[-1]], gap)
    return out


def _taper(out: list[float], i: int, step: int, s: float, gap: int) -> None:
    for k in range(1, gap + 1):
        out[i + step * k] = s * (1 - k / (gap + 1))


def backtracks(pts: list[Point], new: list[Point]) -> list[int]:
    """Indexy i, kde posunutá čára mezi i−1 a i couvne proti směru původní lajny
    (smyčka z posunu v ostré zatáčce)."""
    out = []
    for i in range(1, len(new)):
        t = _frame(pts, i, 1)
        dx, dy = _local(new[i - 1], new[i])
        if dx * t[0] + dy * t[1] < 0:
            out.append(i)
    return out


def _offset(p: Point, t: tuple[float, float], s: float) -> Point:
    nx, ny = -t[1] * s, t[0] * s  # normála vlevo od směru
    return p[0] + nx / (M_PER_DEG * math.cos(math.radians(p[1]))), p[1] + ny / M_PER_DEG


def smooth(pts: list[Point]) -> list[Point]:
    """Jedno vyhlazení 1-2-1 (konce zůstanou) – serpentiny se tím skoro nezkrátí."""
    if len(pts) < 3:
        return list(pts)
    return [pts[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                       for a, b, c in zip(pts, pts[1:], pts[2:])] + [pts[-1]]


def gate(cov0: int, cov1: int, len0: float, len1: float, shift_m: float) -> str | None:
    """Důvod odmítnutí narovnané lajny (gain / length / shift), nebo None = přijmout."""
    if cov1 < cov0 + FIT_MIN_GAIN or cov1 < FIT_MIN_COV:
        return "gain"
    if not FIT_LEN_RANGE[0] * len0 <= len1 <= FIT_LEN_RANGE[1] * len0:
        return "length"
    if shift_m > FIT_MAX_SHIFT_M:
        return "shift"
    return None


def fit_to_traces(coords: list[Point], grid: Grid, cov0: int = 0,
                  prefer: set[int] | None = None) -> tuple[list[Point] | None, str | None, int]:
    """Narovná lajnu podle hřbetu stop. Vrací (souřadnice, None, medián posunu v m) při přijetí,
    jinak (None, důvod, medián posunu); důvod „traces“ = posunula se méně než polovina bodů,
    „shape“ = posunutá čára někde couvá (smyčka), jinak důvod z gate()."""
    pts = resample(coords, FIT_STEP_M)
    raw = ridge_shifts(pts, grid, prefer)
    shifts = reject_jumps(raw)
    if sum(s is not None for s in shifts) < max(3, len(pts) // 2):
        return None, "traces", 0
    for _ in range(LOOP_FIXES + 1):
        new = smooth([_offset(p, _frame(pts, i), s) for i, (p, s) in enumerate(zip(pts, fill_gaps(shifts)))])
        bad = backtracks(pts, new)
        if not bad:
            break
        for i in bad:  # body kolem smyčky zpátky na OSM a znovu
            shifts[i - 1] = shifts[i] = None
    shift = round(statistics.median(haversine_m(a, b) for a, b in zip(pts, new)))
    reason = "shape" if bad else gate(cov0, coverage(new, grid), length(coords), length(new), shift)
    if reason:
        return None, reason, shift
    return [(round(x, 6), round(y, 6)) for x, y in new], None, shift


def join_segments(feats: list[dict]) -> list[dict]:
    """Spojí navazující úseky stejné lajny (stejný park, název, obtížnost, zdroj) do jedné
    LineString: OSM lajny bývají rozsekané na kousky po pár metrech, které se samostatně
    nedají hodnotit ani narovnat. Pořadí a směr se dohledá podle koncových bodů."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in feats:
        p = f["properties"]
        key = (p.get("park_id"), p.get("name"), p.get("difficulty"), p.get("source"), bool(p.get("access")), bool(p.get("connector")))
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
        if not f["properties"].get("access") and not f["properties"].get("connector"):
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
        grid = Grid.of(traces)
        covs, shifts = [], []
        rejected = {"traces": 0, "shape": 0, "gain": 0, "length": 0, "shift": 0}
        for f in feats:
            coords = [tuple(c[:2]) for c in f["geometry"]["coordinates"]]
            cov = coverage(coords, grid)
            if fit and cov < FIT_BELOW and length(coords) >= FIT_MIN_LEN_M and not f["properties"].get("snapped"):
                new, reason, shift = fit_to_traces(coords, grid, cov, following_segments(coords, grid))
                if new:
                    f["geometry"]["coordinates"] = [list(c) for c in new]
                    f["properties"]["length_m"] = round(length(new))
                    f["properties"]["gps_fit"] = True
                    f["properties"]["gps_fit_shift_m"] = shift
                    cov = coverage(new, grid)
                    shifts.append(shift)
                else:
                    rejected[reason] += 1
            f["properties"]["gps_cov"] = cov
            covs.append(cov)
        covs.sort()
        low = [f for f in feats if f["properties"]["gps_cov"] < LOW_COV]
        summary[pid] = {"points": n_points(traces), "trails": len(feats), "median": covs[len(covs) // 2],
                        "fitted": len(shifts), "shift": round(statistics.median(shifts)) if shifts else None,
                        "rejected": rejected,
                        "low": sorted({f["properties"].get("name") or "bez názvu" for f in low})}
    return summary


def render(summary: dict[str, dict]) -> str:
    out = ["# Lajny × GPS stopy OSM", "",
           "| park | bodů stop | úseků | medián pokrytí | narovnáno | medián posunu | úseky pod 30 % |",
           "|---|---:|---:|---:|---:|---:|---|"]
    for pid, s in summary.items():
        if "error" in s:
            out.append(f"| `{pid}` | – | {s['trails']} | – | – | – | chyba: {s['error']} |")
            continue
        low = ", ".join(s["low"][:8]) + (" …" if len(s["low"]) > 8 else "")
        shift = f"{s['shift']} m" if s.get("shift") is not None else "–"
        out.append(f"| `{pid}` | {s['points']:,} | {s['trails']} | {s['median']} % | {s.get('fitted', 0)} | {shift} | {low or '–'} |")
    out += ["", f"Pokrytí = podíl bodů lajny (po 25 m), u kterých je do {TOL_M} m bod veřejné GPS stopy. "
                "Nízké pokrytí: bez provozu, zavřeno, nové, nebo špatně zakreslené. "
                "Medián posunu = jak daleko se narovnané lajny v parku průměrně (medián) posunuly od OSM.", "",
            "Stopy © přispěvatelé OpenStreetMap, ODbL."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path, help="trails.geojson (doplní se gps_cov)")
    ap.add_argument("--cache", type=Path, help="JSON cache stop podle obdélníku")
    ap.add_argument("--report", type=Path, help="markdown report")
    ap.add_argument("--fit", action="store_true",
                    help="lajny s pokrytím pod 85 %% posunout na hřbet hustoty stop, když to pokrytí zvedne na 70 %%+")
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
