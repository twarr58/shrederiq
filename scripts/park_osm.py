#!/usr/bin/env python3
"""Data parků z OpenStreetMap pro web: lajny, základna (dolní stanice lanovky) a parkoviště.

Na rozdíl od fetch_osm_parks.py (hledá parky v celé ČR) se tady ptáme Overpass
přímo kolem každého parku z web/data/parks.geojson (RADIUS_M):

  lajny       – cesty s mtb:scale:imba / mtb:scale:dh / mtb=designated,
                jednosměrné pěšiny a cyklostezky se zákazem chůze (šlapací traily),
                pěšiny a cesty s mtb:scale do LIFT_TRAIL_M od lanovky (bikeparky),
                členové relací route=mtb (lajny trail center bývají zmapované jen
                jako relace; název a barva značení (colour=*) se vezmou z relace). Dlouhé cyklotrasy
                (network=lcn/rcn/ncn/icn bez „trailového“ názvu, > MAX_ROUTE_KM) se vyřadí.
  základna    – dolní stanice lanovky, u které je nejvíc lajn (cesta aerialway se
                v OSM kreslí směrem nahoru, takže dolní stanice je její první bod);
                bez lanovky parkoviště nejblíž lajnám.
  parkoviště  – nejbližší amenity=parking do PARKING_M od základny.

Výstup:
  --trails  web/data/trails.geojson   (park_id, name, difficulty, access, length_m)
  --info    web/data/parks_osm.json   ({park_id: {base, base_source, lift, parking, parking_name}})
  --summary Markdown tabulka pro kontrolu ('-' = stdout)

Příklad:
  python3 scripts/park_osm.py --cache /tmp/parks.json --trails web/data/trails.geojson --info web/data/parks_osm.json --summary -
"""
from __future__ import annotations

import argparse
import heapq
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import (  # noqa: E402
    DIFFICULTIES, PARKS, OverpassError, difficulty, fetch_overpass, haversine_m, is_purpose_built, name_tokens, simplify,
)

RADIUS_M = 3000
LIFT_TRAIL_M = 1500
PARKING_M = 700
NEAR_LIFT_M = 2000   # lanovka bez lajn v okolí se vezme jako základna jen takhle blízko bodu parku
MIN_LIFT_SCORE = 10  # méně bodů lajn u lanovky je šum (Hofmanky Express: 3 body vs. gondola s 0)
LIFT_PRIORITY = {"gondola": 0, "cable_car": 0, "mixed_lift": 0, "chair_lift": 1}  # bikeparky jezdí na velké lanovce
MAX_ROUTE_KM = 25
LIFTS = "chair_lift|gondola|cable_car|mixed_lift|drag_lift|t-bar|j-bar|platter"
BIKE_LIFTS = {"chair_lift", "gondola", "cable_car", "mixed_lift"}  # kola vozí hlavně tyhle
TRAIL_NAME_RE = re.compile(r"singl|single|trail|flow|bike ?park|enduro|downhill|freeride|gravity|pump ?track|\bline\b|\bdh\b", re.I)
LONG_NETWORKS = {"lcn", "rcn", "ncn", "icn"}
BATCH = 4            # parků na jeden dotaz – velký dotaz na všechny parky Overpass nezvládal
QUERY_TIMEOUT = 300


def build_query(parks: list[dict], radius: int = RADIUS_M) -> str:
    """Jeden dotaz pro všechny parky (sjednocení dotazů „around“ kolem každého bodu)."""
    pts = [f["geometry"]["coordinates"] for f in parks]
    ar = [f"around:{radius},{lat:.5f},{lon:.5f}" for lon, lat in pts]
    ways = "\n".join(
        f'  way({a})["highway"~"^(path|track|cycleway|bridleway|footway)$"]["mtb:scale"];\n'
        f'  way({a})["mtb:scale:imba"];\n'
        f'  way({a})["mtb:scale:dh"];\n'
        f'  way({a})["highway"="path"]["mtb"];\n'
        f'  way({a})["highway"="path"]["bicycle"="designated"];'
        for a in ar)
    rels = "\n".join(f'  relation({a})["route"="mtb"];' for a in ar)
    lifts = "\n".join(f'  way({a})["aerialway"~"^({LIFTS})$"];' for a in ar)
    parking = "\n".join(f'  nwr({a})["amenity"="parking"];' for a in ar)
    return f"""[out:json][timeout:{QUERY_TIMEOUT}][maxsize:536870912];
(
{ways}
)->.w;
.w out tags geom;
(
{rels}
)->.r;
.r out body;
way(r.r);
out tags geom;
(
{lifts}
);
out tags geom;
(
{parking}
);
out tags center;
"""


def fetch_batched(parks: list[dict], fetch=None, batch: int = BATCH) -> dict:
    """Dotazy po dávkách parků; prvky se sloučí (stejný prvek z víc dávek jen jednou)."""
    fetch = fetch or (lambda q: fetch_overpass(q, check=check, timeout=QUERY_TIMEOUT + 60))
    seen, elements = set(), []
    for i in range(0, len(parks), batch):
        chunk = parks[i:i + batch]
        data = fetch(build_query(chunk))
        new = 0
        for el in data.get("elements", []):
            key = (el["type"], el["id"])
            if key not in seen:
                seen.add(key)
                elements.append(el)
                new += 1
        print(f"overpass dávka {i // batch + 1}/{-(-len(parks) // batch)} "
              f"({', '.join(f['properties']['id'] for f in chunk)}): {new} nových prvků", file=sys.stderr)
    return {"elements": elements}


def check(data: dict) -> dict:
    remark = (data.get("remark") or "").strip()
    if remark:
        print(f"overpass remark: {remark}", file=sys.stderr)
    if "error" in remark.lower():
        raise OverpassError(remark)
    if not data.get("elements"):
        raise OverpassError("prázdná odpověď")
    return data


# --- parsování ----------------------------------------------------------------

def _coords(el: dict) -> list[tuple[float, float]]:
    return [(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]


def _center(el: dict) -> tuple[float, float] | None:
    c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
    return (c["lat"], c["lon"]) if c else None


def parse(data: dict) -> dict:
    ways, rels, lifts, parkings = {}, {}, {}, {}
    for el in data.get("elements", []):
        t, tags = el["type"], el.get("tags", {})
        if t == "relation" and tags.get("route") == "mtb":
            rels[el["id"]] = {"tags": tags, "ways": [m["ref"] for m in el.get("members", []) if m.get("type") == "way"]}
        elif tags.get("amenity") == "parking":
            c = _center(el)
            if c:
                parkings[(t, el["id"])] = {"pos": c, "name": tags.get("name")}
        elif t == "way" and "aerialway" in tags:
            coords = _coords(el)
            if len(coords) >= 2:
                lifts[el["id"]] = {"tags": tags, "coords": coords}
        elif t == "way":
            coords = _coords(el)
            if len(coords) >= 2:
                ways[el["id"]] = {"tags": tags, "coords": coords}
    return {"ways": ways, "rels": rels, "lifts": lifts, "parkings": parkings}


def relation_difficulty(tags: dict) -> str | None:
    """Obtížnost z relace route=mtb: značení parku (colour=blue…) má přednost před
    mtb:scale jednotlivých úseků – Peklák má Koridu s mtb:scale 3 (černá), ale značenou červeně."""
    colour = (tags.get("colour") or tags.get("color") or "").lower()
    return colour if colour in DIFFICULTIES else difficulty(tags)


def trail_relations(osm: dict, difficulties: dict[int, str] | None = None) -> dict[int, str | None]:
    """Way id → název relace pro členy „trailových“ relací route=mtb.
    Do `difficulties` (way id → obtížnost) doplní barvu značení z relace, pokud ji má."""
    out: dict[int, str | None] = {}
    for rel in osm["rels"].values():
        tags, name = rel["tags"], rel["tags"].get("name")
        members = [osm["ways"][w] for w in rel["ways"] if w in osm["ways"]]
        km = sum(_length(w["coords"]) for w in members) / 1000
        trailish = bool(name and TRAIL_NAME_RE.search(name))
        # relace, jejíž členové leží i mimo okolí parků, je dálková trasa (MTB-Tour, okruh
        # kolem jezera) – délka spočítaná jen z blízkých členů by ji pustila dovnitř
        beyond = len(members) < len(rel["ways"])
        if not trailish and (tags.get("network") in LONG_NETWORKS or km > MAX_ROUTE_KM or beyond):
            continue
        diff = relation_difficulty(tags)
        for w in rel["ways"]:
            out.setdefault(w, name)
            if diff and difficulties is not None:
                difficulties.setdefault(w, diff)
    return out


def _length(coords) -> float:
    return sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))


def _dist_to_line(p, coords) -> float:
    return min(haversine_m(*p, *c) for c in coords)


# --- výběr pro jeden park -----------------------------------------------------

def select_trails(park_pt, osm, rel_names, radius=RADIUS_M, has_lift=None):
    """Lajny parku: way id → (way, název z relace). Park bez lanovky (lift: false)
    nebere cesty, které by sem patřily jen kvůli blízké lanovce."""
    lifts = [] if has_lift is False else [
        l["coords"] for l in osm["lifts"].values()
        if l["tags"].get("aerialway") in BIKE_LIFTS and _dist_to_line(park_pt, l["coords"]) <= radius + LIFT_TRAIL_M]
    out = {}
    for wid, w in osm["ways"].items():
        tags, coords = w["tags"], w["coords"]
        if _dist_to_line(park_pt, coords[::3] + [coords[-1]]) > radius:
            continue
        in_rel = wid in rel_names
        hw, name = tags.get("highway"), tags.get("name") or tags.get("mtb:name") or ""
        strong = ("mtb:scale:imba" in tags or "mtb:scale:dh" in tags
                  # jednosměrná pěšina / cyklostezka se zákazem chůze je postavená pro kola (šlapací traily)
                  or is_purpose_built(tags)
                  # pěšina s jakýmkoli mtb=* (designated, single track, yes…) je MTB lajna;
                  # bicycle=designated pěšina jen s mtb:scale nebo s „lajnovým“ názvem (Gravity Line, Fun Trail)
                  or (hw == "path" and tags.get("mtb") not in (None, "no"))
                  or (hw == "path" and tags.get("bicycle") == "designated"
                      and ("mtb:scale" in tags or TRAIL_NAME_RE.search(name))))
        # pěšina s mtb:scale u lanovky – ale ne zákaz vjezdu (bicycle=no) a ne skialpová /
        # běžkařská stopa s mtb:scale=0 (Špičák: piste:type=skitour)
        rideable = tags.get("bicycle") != "no" and not (tags.get("mtb:scale") == "0" and "piste:type" in tags)
        near_lift = "mtb:scale" in tags and rideable and any(
            _dist_to_line(c, l) <= LIFT_TRAIL_M for l in lifts for c in coords[::4] + [coords[-1]])
        if in_rel or strong or near_lift:
            out[wid] = (w, rel_names.get(wid))
    return add_named_siblings(park_pt, osm, out, radius)


# --- spojky mezi kusy lajny ---------------------------------------------------
# OSM často nemá celou lajnu: výjezd (Šembeřské stezky: Stoupák) vede kus po neznačené
# lesní cestě, dva kusy téže lajny dělí pár metrů pěšiny. Spojka se hledá po síti cest
# (pěšiny, lesní cesty, cyklostezky) a kreslí se čárkovaně jako neznačený úsek lajny.

NETWORK_HIGHWAYS = {"path", "track", "cycleway", "footway", "bridleway"}
GAP_M = 150           # největší mezera mezi kusy jedné lajny, která se spojí
UPHILL_LINK_M = 300   # výjezd se napojí na začátek jiné lajny nejvýš tak daleko (vzdušně)
DETOUR = 2.5          # spojka smí být nejvýš tolikrát delší než vzdušná vzdálenost (+ SNAP_M)
SNAP_M = 25           # konec lajny se napojí na síť cest do této vzdálenosti
UPHILL_RE = re.compile(r"stoup|uphill|climb|vyjezd|výjezd", re.I)
TOUCH_M = 8           # konec kusu do této vzdálenosti od jiného kusu = kusy už navazují (odbočka v půlce)
OVERLAP = 0.5         # spojka, která z víc než poloviny vede po už nakreslené lajně, se zahodí


class Network:
    """Graf cest z OSM: uzel = souřadnice vrcholu (sdílené vrcholy spojují cesty)."""

    def __init__(self, osm: dict):
        self.adj: dict[tuple, list[tuple[tuple, float]]] = defaultdict(list)
        for w in osm["ways"].values():
            t = w["tags"]
            if t.get("highway") not in NETWORK_HIGHWAYS or t.get("bicycle") == "no" or t.get("access") in ("no", "private"):
                continue
            pts = [(round(lat, 6), round(lon, 6)) for lat, lon in w["coords"]]
            for a, b in zip(pts, pts[1:]):
                d = haversine_m(*a, *b)
                self.adj[a].append((b, d))
                self.adj[b].append((a, d))
        self.nodes = list(self.adj)

    def nearest(self, p: tuple[float, float], max_m: float = SNAP_M):
        best, best_d = None, max_m
        for n in self.nodes:
            if abs(n[0] - p[0]) > 0.0004 or abs(n[1] - p[1]) > 0.0006:
                continue
            d = haversine_m(*n, *p)
            if d <= best_d:
                best, best_d = n, d
        return best

    def path(self, a: tuple[float, float], b: tuple[float, float], limit_m: float):
        """Nejkratší cesta po síti z bodu a do bodu b (lat, lon), nejvýš limit_m; jinak None."""
        na, nb = self.nearest(a), self.nearest(b)
        if na is None or nb is None:
            return None
        dist, prev, heap = {na: 0.0}, {}, [(0.0, na)]
        while heap:
            d, n = heapq.heappop(heap)
            if n == nb:
                out = [n]
                while out[-1] in prev:
                    out.append(prev[out[-1]])
                return [a] + out[::-1] + [b], d
            if d > dist.get(n, float("inf")) or d > limit_m:
                continue
            for m, w in self.adj[n]:
                nd = d + w
                if nd < dist.get(m, float("inf")) and nd <= limit_m:
                    dist[m], prev[m] = nd, n
                    heapq.heappush(heap, (nd, m))
        return None


def _ends(f: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    c = f["geometry"]["coordinates"]
    return (c[0][1], c[0][0]), (c[-1][1], c[-1][0])


def _line(f: dict) -> list[tuple[float, float]]:
    return [(c[1], c[0]) for c in f["geometry"]["coordinates"]]


def _touches(p: tuple[float, float], f: dict) -> bool:
    return _dist_to_line(p, _line(f)) <= TOUCH_M


def _samples(path: list[tuple[float, float]], step: float = 5.0, skip: float = 15.0) -> list[tuple[float, float]]:
    """Body po step metrech podél cesty bez prvních a posledních skip metrů (u konců se
    spojka lajny dotýká vždycky)."""
    total = _length(path)
    out, run = [], 0.0
    for a, b in zip(path, path[1:]):
        seg = haversine_m(*a, *b)
        t = 0.0
        while t <= seg:
            pos = run + t
            if skip <= pos <= total - skip:
                k = t / seg if seg else 0.0
                out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
            t += step
        run += seg
    return out


def _overlaps(path: list[tuple[float, float]], trails: list[dict]) -> bool:
    """Vede spojka většinou po lajně, která už v mapě je?"""
    pts = _samples(path)
    if not pts:
        return False
    lines = [_line(f) for f in trails]
    on = sum(1 for q in pts if any(_dist_to_line(q, ln) <= TOUCH_M for ln in lines))
    return on > OVERLAP * len(pts)


def _connector(src: dict, path: list[tuple[float, float]], length: float) -> dict:
    p = src["properties"]
    return {"type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in path]},
            "properties": {"park_id": p["park_id"], "name": p["name"], "difficulty": p["difficulty"], "access": False,
                           "length_m": round(length), "osm": "connector", "connector": True}}


def add_connectors(features: list[dict], network: Network) -> list[dict]:
    """Spojky pro lajny jednoho parku: mezi kusy lajny se stejným názvem (mezera do GAP_M)
    a z konce výjezdu („Stoupák“) na nejbližší začátek jiné lajny (do UPHILL_LINK_M)."""
    trails = [f for f in features if not f["properties"].get("access") and f["properties"].get("name")]
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in trails:
        groups[_fold(f["properties"]["name"])].append(f)
    out, done = [], set()
    for key, pieces in groups.items():
        # mezery mezi kusy: vždy nejbližší dvojice konců z různých skupin, dokud jde spojit
        comp = {id(f): i for i, f in enumerate(pieces)}

        def merge(f, g):
            old, new = comp[id(g)], comp[id(f)]
            for k, v in comp.items():
                if v == old:
                    comp[k] = new

        # kusy, které na sebe už navazují (konec jednoho leží na druhém), jsou spojené
        for f in pieces:
            for g in pieces:
                if f is not g and comp[id(f)] != comp[id(g)] and any(_touches(e, g) for e in _ends(f)):
                    merge(f, g)
        while True:
            best = None
            for i, f in enumerate(pieces):
                for g in pieces[i + 1:]:
                    if comp[id(f)] == comp[id(g)]:
                        continue
                    for a in _ends(f):
                        for b in _ends(g):
                            d = haversine_m(*a, *b)
                            if 1 < d <= GAP_M and (best is None or d < best[0]):
                                best = (d, f, g, a, b)
            if not best:
                break
            d, f, g, a, b = best
            found = network.path(a, b, DETOUR * d + SNAP_M)
            merge(f, g)
            pair = tuple(sorted((a, b)))
            if found and pair not in done and not _overlaps(found[0], trails + out):
                done.add(pair)
                out.append(_connector(f, *found))
        # výjezd: konec posledního kusu (jednosměrné kusy jdou ve směru jízdy) na začátek sjezdu
        if not UPHILL_RE.search(key):
            continue
        starts = {_ends(f)[0] for f in pieces}
        tops = [_ends(f)[1] for f in pieces if all(haversine_m(*_ends(f)[1], *s) > 20 for s in starts)
                and not any(_touches(_ends(f)[1], g) for g in trails if g is not f)]
        others = [_ends(f)[0] for f in trails if _fold(f["properties"]["name"]) != key]
        best = None
        for top in tops:
            for s in others:
                d = haversine_m(*top, *s)
                if 20 < d <= UPHILL_LINK_M and (best is None or d < best[0]):
                    best = (d, top, s)
        if best:
            d, top, s = best
            found = network.path(top, s, DETOUR * d + SNAP_M)
            if found and not _overlaps(found[0], trails + out):
                src = next(f for f in pieces if _ends(f)[1] == top)
                out.append(_connector(src, *found))
    return out


def inherit_difficulty(features: list[dict]) -> None:
    """Úsek bez obtížnosti převezme obtížnost nejdelšího úseku lajny se stejným názvem."""
    best: dict[str, tuple[int, str]] = {}
    for f in features:
        p = f["properties"]
        if p["name"] and p["difficulty"] and p["length_m"] > best.get(_fold(p["name"]), (-1, ""))[0]:
            best[_fold(p["name"])] = (p["length_m"], p["difficulty"])
    for f in features:
        p = f["properties"]
        if p["name"] and not p["difficulty"] and _fold(p["name"]) in best:
            p["difficulty"] = best[_fold(p["name"])][1]


SIBLING_HIGHWAYS = {"path", "cycleway", "bridleway", "footway"}
SIBLING_M = 60  # pokračování lajny začíná nebo končí nejvýš tak daleko od lajny se stejným názvem


def _fold(name: str) -> str:
    return " ".join(name.lower().split())


def add_named_siblings(park_pt, osm, out, radius=RADIUS_M):
    """Pěšina se stejným názvem jako vybraná lajna, která na ni navazuje, je její částí,
    i když nemá MTB značky (Peklák: kus Koridy, Drtikolu a Vyhlídky jen s oneway=yes).
    Opakuje se, dokud přibývají (řetěz úseků)."""
    while True:
        by_name: dict[str, list] = {}
        for wid, (w, rel_name) in out.items():
            name = w["tags"].get("name") or w["tags"].get("mtb:name") or rel_name
            if name and w["tags"].get("highway") != "track":
                by_name.setdefault(_fold(name), []).append(w["coords"])
        added = 0
        for wid, w in osm["ways"].items():
            tags = w["tags"]
            name = tags.get("name") or tags.get("mtb:name")
            if (wid in out or not name or tags.get("highway") not in SIBLING_HIGHWAYS
                    or tags.get("bicycle") == "no" or _fold(name) not in by_name):
                continue
            ends = [w["coords"][0], w["coords"][-1]]
            if _dist_to_line(park_pt, w["coords"][::3] + [w["coords"][-1]]) > radius:
                continue
            if any(_dist_to_line(e, line) <= SIBLING_M for e in ends for line in by_name[_fold(name)]):
                out[wid] = (w, None)
                added += 1
        if not added:
            return out


def lift_candidates(park_pt, trails, osm, has_lift=True):
    """Lanovky pro kola do RADIUS_M od bodu parku: (lanovka, počet bodů lajn do 400 m od ní,
    vzdálenost dolní stanice od bodu parku), seřazené podle skóre. Park s lift: false
    lanovky nedostane."""
    if has_lift is False:
        return []
    pts = [c for w, _ in trails.values() for c in w["coords"][::3]]
    out = []
    for l in osm["lifts"].values():
        if l["tags"].get("aerialway") not in BIKE_LIFTS or _dist_to_line(park_pt, l["coords"]) > RADIUS_M:
            continue
        score = sum(1 for p in pts if _dist_to_line(p, l["coords"]) <= 400)
        out.append((l, score, haversine_m(*park_pt, *l["coords"][0])))
    # nejvíc lajn, při shodě nejblíž bodu parku
    return sorted(out, key=lambda c: (-c[1], c[2]))


def park_keywords(park_name, town=None) -> str:
    """Slova z názvu parku, která nejsou jménem obce – „Bikepark Špindlerův Mlýn“ v obci
    Špindlerův Mlýn nedává nic (lanovky se jmenují po městě), „Bikepark Kopřivná“ dá kopřivná."""
    return " ".join(name_tokens(park_name) - name_tokens(town))


def choose_base(park_pt, trails, osm, has_lift=True, park_name=None, town=None):
    """Základna parku (OSM kreslí lanovky zdola nahoru, dolní stanice = první bod):
      1. lanovka, jejíž název sdílí slovo s názvem parku, které není jménem obce
         (Kopřivná, Špičák); mezi víc takovými ta s nejvíc lajnami, pak nejbližší,
      2. lanovka s nejvíc lajnami do 400 m, pokud jich je aspoň MIN_LIFT_SCORE bodů,
      3. jinak lanovka do NEAR_LIFT_M od bodu parku: gondola před sedačkou, pak nejbližší,
      4. jinak vlek (kotva, poma) se jménem parku do NEAR_LIFT_M (Kyčerka, Peklák),
      5. jinak parkoviště nejblíž lajnám (když jich je dost), případně bodu parku.
    Vrací i kandidáty lanovek pro kontrolu v reportu."""
    cands = lift_candidates(park_pt, trails, osm, has_lift)
    cand_info = [(l["tags"].get("name") or f"way/{lid}", score, round(dist))
                 for (l, score, dist), lid in ((c, next(k for k, v in osm["lifts"].items() if v is c[0])) for c in cands[:3])]
    if not cands and has_lift is not False:
        # žádná lanovka pro kola – ukázat aspoň vleky v okolí, ať je vidět, jestli v OSM chybí nebo je jinak otagovaná
        others = sorted((haversine_m(*park_pt, *l["coords"][0]), l) for l in osm["lifts"].values()
                        if l["tags"].get("aerialway") not in BIKE_LIFTS)
        cand_info = [(f"{l['tags'].get('name') or 'bez názvu'} [{l['tags'].get('aerialway')}]", -1, round(d))
                     for d, l in others[:3] if d <= NEAR_LIFT_M]

    keywords = name_tokens(park_keywords(park_name, town))
    named = [(score, dist, l) for l, score, dist in cands if keywords & name_tokens(l["tags"].get("name"))]
    best = max(named, key=lambda x: (x[0], -x[1]))[2] if named else None
    if not best:
        best = next((l for l, score, _ in cands if score >= MIN_LIFT_SCORE), None)
    if not best:
        near = [(LIFT_PRIORITY.get(l["tags"].get("aerialway"), 2), dist, l) for l, _, dist in cands if dist <= NEAR_LIFT_M]
        best = min(near, key=lambda x: (x[0], x[1]))[2] if near else None
    if not best and has_lift is not False:
        # 5. vlek (kotva, poma) se jménem parku do NEAR_LIFT_M – menší bikeparky vozí kola vlekem (Kyčerka, Peklák)
        drag = [(haversine_m(*park_pt, *l["coords"][0]), l) for l in osm["lifts"].values()
                if l["tags"].get("aerialway") not in BIKE_LIFTS and keywords & name_tokens(l["tags"].get("name"))]
        drag = [x for x in drag if x[0] <= NEAR_LIFT_M]
        best = min(drag, key=lambda x: x[0])[1] if drag else None
    if best:
        return {"base": best["coords"][0], "base_source": "lift", "lift": best["tags"].get("name"), "candidates": cand_info}

    pts = [c for w, _ in trails.values() for c in w["coords"][::3]]
    refs = [park_pt]
    if len(trails) >= 3:  # pár úseků nestačí – těžiště by ukázalo kamsi do lesa
        refs.insert(0, (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)))
    for ref in refs:
        near = [(haversine_m(*ref, *pk["pos"]), pk) for pk in osm["parkings"].values()]
        near = [x for x in near if x[0] <= 1500]
        if near:
            pk = min(near, key=lambda x: x[0])[1]
            return {"base": pk["pos"], "base_source": "parking", "lift": None, "candidates": cand_info}
    return {"base": None, "base_source": None, "lift": None, "candidates": cand_info}


def choose_parking(base, osm):
    if not base:
        return None
    near = [(haversine_m(*base, *pk["pos"]), pk) for pk in osm["parkings"].values()]
    near = [x for x in near if x[0] <= PARKING_M]
    return min(near, key=lambda x: x[0])[1] if near else None


def build(parks: list[dict], osm: dict):
    rel_diff: dict[int, str] = {}
    rel_names = trail_relations(osm, rel_diff)
    pts = {f["properties"]["id"]: (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) for f in parks}
    has_lift = {f["properties"]["id"]: f["properties"].get("lift") for f in parks}
    park_names = {f["properties"]["id"]: f["properties"].get("name") for f in parks}
    towns = {f["properties"]["id"]: f["properties"].get("town") for f in parks}

    # Každou cestu jen k jednomu parku – k tomu, jehož základně (nebo bodu, když základnu
    # nemá) je nejblíž. Naše body parků bývají mimo, proto se nejdřív hledá základna.
    per_park = {pid: select_trails(pt, osm, rel_names, has_lift=has_lift[pid]) for pid, pt in pts.items()}
    anchor = {pid: choose_base(pts[pid], per_park[pid], osm, has_lift[pid], park_names[pid], towns[pid])["base"] or pts[pid] for pid in pts}
    owner: dict[int, str] = {}
    for pid, cands in per_park.items():
        for wid, (w, _) in cands.items():
            d = _dist_to_line(anchor[pid], w["coords"])
            if wid not in owner or d < _dist_to_line(anchor[owner[wid]], w["coords"]):
                owner[wid] = pid

    network = Network(osm)
    features, info, rows = [], {}, []
    for f in parks:
        pid = f["properties"]["id"]
        trails = {wid: v for wid, v in per_park[pid].items() if owner.get(wid) == pid}
        base = choose_base(pts[pid], trails, osm, has_lift[pid], park_names[pid], towns[pid])
        parking = choose_parking(base["base"], osm)
        entry = {"base_source": base["base_source"], "lift": base["lift"]}
        if base["base"]:
            entry["base"] = [round(base["base"][1], 5), round(base["base"][0], 5)]
        if parking:
            entry["parking"] = [round(parking["pos"][1], 5), round(parking["pos"][0], 5)]
            entry["parking_name"] = parking["name"]
        info[pid] = entry

        names = set()
        for wid, (w, rel_name) in sorted(trails.items()):
            tags = w["tags"]
            name = tags.get("name") or tags.get("mtb:name") or rel_name
            # lesní cesta (track) je přístupová, i když má mtb:scale nebo jméno – lajna z ní je
            # jen s mtb:scale:imba / dh nebo jako člen trailové relace
            access = (tags.get("highway") == "track" and "mtb:scale:imba" not in tags
                      and "mtb:scale:dh" not in tags and wid not in rel_names)
            if access:
                name = None  # jméno lesní cesty v seznamu lajn mate
            if name:
                names.add(name)
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in simplify(w["coords"])]},
                "properties": {"park_id": pid, "name": name, "difficulty": rel_diff.get(wid) or difficulty(tags), "access": access,
                               "length_m": round(_length(w["coords"])), "osm": f"way/{wid}"},
            })
        inherit_difficulty([x for x in features if x["properties"]["park_id"] == pid])
        features.extend(add_connectors([x for x in features if x["properties"]["park_id"] == pid], network))
        moved = round(haversine_m(*pts[pid], *base["base"])) if base["base"] else None
        rows.append((pid, f["properties"]["name"], base["base_source"], base["lift"], moved,
                     parking["name"] if parking and parking["name"] else ("ano" if parking else "–"),
                     len(names), len(trails), base["candidates"]))
    trails_fc = {"type": "FeatureCollection", "attribution": "© přispěvatelé OpenStreetMap, ODbL", "features": features}
    return trails_fc, info, rows


def dump_park(pid: str, parks: list[dict], osm: dict, trails_fc: dict, radius_m: float = 2000) -> None:
    """Diagnostika: každá cesta do radius_m od bodu parku, její tagy a kam ji build zařadil."""
    f = next((f for f in parks if f["properties"]["id"] == pid), None)
    if not f:
        print(f"dump: park {pid} není v parks.geojson", file=sys.stderr)
        return
    pt = (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
    owner = {int(x["properties"]["osm"].split("/")[1]): x["properties"]["park_id"] for x in trails_fc["features"]}
    keep = ("highway", "name", "mtb:name", "mtb:scale", "mtb:scale:imba", "mtb:scale:dh", "mtb", "bicycle", "piste:type", "access")
    print(f"dump {pid}: cesty do {radius_m:.0f} m od bodu {pt}", file=sys.stderr)
    for wid, w in sorted(osm["ways"].items(), key=lambda kv: _dist_to_line(pt, kv[1]["coords"])):
        d = _dist_to_line(pt, w["coords"])
        if d > radius_m:
            break
        tags = " ".join(f"{k}={v}" for k, v in w["tags"].items() if k in keep)
        print(f"  way/{wid} {d:5.0f} m {len(w['coords']):3d} b  → {owner.get(wid, '–'):24} {tags}", file=sys.stderr)
    for lid, l in osm["lifts"].items():
        d = _dist_to_line(pt, l["coords"])
        if d <= radius_m:
            print(f"  lift way/{lid} {d:5.0f} m {l['tags'].get('aerialway')} {l['tags'].get('name', '')}", file=sys.stderr)


def render_summary(rows) -> str:
    out = ["# Parky × OSM (lajny, základna, parkoviště)", "",
           "| id | park | základna | lanovka | posun bodu | parkoviště | pojm. lajn | úseků OSM | kandidáti lanovek (lajn u ní / m od bodu) |",
           "|---|---|---|---|---:|---|---:|---:|---|"]
    for pid, name, src, lift, moved, parking, named, segs, cands in rows:
        src_l = {"lift": "lanovka", "parking": "parkoviště", None: "–"}[src]
        cands_l = "; ".join(f"{n} ({d:,} m)" if sc < 0 else f"{n} ({sc} / {d:,} m)" for n, sc, d in cands) or "–"
        out.append(f"| `{pid}` | {name} | {src_l} | {lift or '–'} | {f'{moved:,} m' if moved is not None else '–'} "
                   f"| {parking} | {named} | {segs} | {cands_l} |")
    out += ["", "Kandidáti = lanovky pro kola do 3 km od bodu parku, nejvýš tři. Základna: lanovka se jménem parku (ne obce); "
                "jinak ta s nejvíc lajnami (aspoň 10 bodů); jinak do 2 km od bodu gondola před sedačkou, pak nejbližší; "
                "jinak vlek se jménem parku; jinak parkoviště. Vlek v hranatých závorkách = v okolí není lanovka pro kola, jen tenhle typ.",
            "", "Data © přispěvatelé OpenStreetMap, ODbL."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--cache", type=Path, help="odpověď Overpass: když existuje, použije se; jinak se do ní uloží")
    ap.add_argument("--trails", type=Path)
    ap.add_argument("--info", type=Path)
    ap.add_argument("--summary", help="Markdown souhrn ('-' = stdout)")
    ap.add_argument("--dump-park", help="diagnostika: vypsat cesty do 2 km od bodu parku s tagy a výsledkem výběru")
    args = ap.parse_args(argv)

    parks = json.loads(args.parks.read_text())["features"]
    if args.cache and args.cache.exists():
        data = check(json.loads(args.cache.read_text()))
    else:
        data = fetch_batched(parks)
        if args.cache:
            args.cache.write_text(json.dumps(data, ensure_ascii=False))

    osm = parse(data)
    print(f"OSM kolem parků: {len(osm['ways'])} úseků, {len(osm['rels'])} relací route=mtb, "
          f"{len(osm['lifts'])} lanovek a vleků, {len(osm['parkings'])} parkovišť", file=sys.stderr)
    trails_fc, info, rows = build(parks, osm)
    if args.dump_park:
        dump_park(args.dump_park, parks, osm, trails_fc)
    with_trails = sum(1 for r in rows if r[7])
    print(f"trails.geojson: {len(trails_fc['features'])} úseků, lajny má {with_trails} z {len(rows)} parků; "
          f"základna u {sum(1 for r in rows if r[2])} parků", file=sys.stderr)

    if args.trails:
        args.trails.write_text(json.dumps(trails_fc, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.info:
        args.info.write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n")
    if args.summary:
        text = render_summary(rows)
        if args.summary == "-":
            sys.stdout.write(text)
        else:
            Path(args.summary).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
