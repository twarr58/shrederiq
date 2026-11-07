#!/usr/bin/env python3
"""Najde bikeparky, trailparky a singltreky v ČR v OpenStreetMap (Overpass API)
a porovná je s web/data/parks.geojson.

V OSM má tag leisure=bike_park jen malá část parků. Většina je zmapovaná jen jako
jednotlivé lajny. Skript proto:

  1. stáhne lajny, které jsou typické pro bikeparky a trail centra:
       - mtb:scale:dh=*         sjezdové (bikepark) lajny,
       - mtb:scale:imba=*       singltreky / trailparky (IMBA škála),
       - highway=path + mtb=designated,
       - MTB cesty (mtb:scale) do 1,5 km od lanovek – bikeparky u lanovek
         mívají lajny otagované jen takhle,
  2. stáhne „kotvy“ s názvem: leisure=bike_park, sportoviště se sport=mtb / downhill
     a MTB trasy (route=mtb) s názvem typu Singltrek / Trail / Bikepark,
  3. lajny ležící blízko sebe spojí do shluků; shluk = kandidát na park
     (střed, počet pojmenovaných lajn a úseků OSM, délka, typ, název z kotvy),
  4. shluky spáruje s naším seznamem podle vzdálenosti a podobnosti názvu;
     shluky bez názvu pojmenuje podle nejbližší obce.

Výstup:
  --report   Markdown report: shody (odchylka polohy), naše parky bez shluku,
             shluky, které v seznamu chybí (kandidáti),
  --geojson  shluky jako GeoJSON body,
  --trails   lajny všech shluků jako GeoJSON LineString s cluster_id,
  --park-trails  lajny spárovaných parků s park_id a obtížností pro web
             (web/data/trails.geojson, generuje workflow pages).

Skript jen navrhuje, parks.geojson neupravuje. Bez závislostí (jen stdlib).

Příklad:
  python3 scripts/fetch_osm_parks.py --cache osm.json --report - --geojson clusters.geojson
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
USER_AGENT = "shrederiq/0.1 (+https://github.com/twarr58/shrederiq)"
ROOT = Path(__file__).resolve().parent.parent
PARKS = ROOT / "web" / "data" / "parks.geojson"

LINK_M = 600            # lajny blíž než tohle patří do stejného shluku (vzdálenost bodů geometrie)
MIN_TRAILS = 3          # menší shluky ignorovat (osamocené lajny, pumptracky)
MIN_LENGTH_M = 1500
ANCHOR_RADIUS_M = 1500  # kotva (název) se k shluku přiřadí do této vzdálenosti
MATCH_RADIUS_M = 5000   # párování s naším seznamem
NONAME_MATCH_M = 3000   # bez shody v názvu párovat jen do této vzdálenosti
LIFT_RADIUS_M = 1500

ROUTE_NAME_RE = r"[Ss]ingl|[Ss]ingle|[Tt]rail|[Bb]ike ?park|[Ff]low|[Ee]ndur"

QUERY = f"""
[out:json][timeout:900][maxsize:536870912];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
(
  way["mtb:scale:dh"](area.cz);
  way["mtb:scale:imba"](area.cz);
  way["highway"="path"]["mtb"="designated"](area.cz);
);
out tags geom;
// bikeparky u lanovek mívají lajny jen s mtb:scale – bereme MTB cesty do 1,5 km od lanovky
way["aerialway"~"^(chair_lift|gondola|cable_car|mixed_lift)$"](area.cz)->.lifts;
way(around.lifts:{LIFT_RADIUS_M})["highway"~"^(path|track)$"]["mtb:scale"];
out tags geom;
node["place"~"^(town|village)$"](area.cz);
out;
(
  .lifts;
  nwr["leisure"="bike_park"](area.cz);
  nwr["sport"~"(^|;)(mtb|downhill|bmx)($|;)"]["leisure"~"^(sports_centre|track|pitch)$"](area.cz);
  relation["route"="mtb"]["name"~"{ROUTE_NAME_RE}"](area.cz);
);
out tags center;
"""


# --- obecné pomůcky -----------------------------------------------------------

def fold(s: str | None) -> str:
    """'Bikepark Špičák' → 'bikepark spicak'"""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


STOPWORDS = {"bikepark", "bike", "park", "trail", "trailpark", "trails", "singltrek", "singletrack",
             "singltreky", "centrum", "center", "mtb", "areal", "pod", "nad", "na", "v", "u", "the"}


def name_tokens(s: str | None) -> set[str]:
    return {t for t in fold(s).split() if t not in STOPWORDS and len(t) > 1}


def name_score(a: str | None, b: str | None) -> float:
    """Jaccard podobnost významných slov názvu (0..1)."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def line_length_m(coords: list[tuple[float, float]]) -> float:
    """coords = [(lat, lon), …]"""
    return sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))


# --- Overpass -----------------------------------------------------------------

class OverpassError(RuntimeError):
    pass


def check_response(data: dict) -> dict:
    """Overpass při timeoutu / nedostatku paměti vrací HTTP 200 s chybou v `remark`
    a prázdnými nebo neúplnými daty – takovou odpověď nelze použít."""
    remark = data.get("remark") or ""
    if remark:
        print(f"overpass remark: {remark.strip()}", file=sys.stderr)
    if "error" in remark.lower():
        raise OverpassError(remark.strip())
    elements = data.get("elements") or []
    if not elements:
        raise OverpassError("prázdná odpověď")
    # Neúplná odpověď bez chyby: lanovky přišly, ale MTB cesty kolem nich ne
    # (dotaz „around“ je nejdražší část a Overpass ho občas tiše vynechá).
    lifts = sum(1 for e in elements if "aerialway" in e.get("tags", {}))
    ways = [e for e in elements if e.get("type") == "way" and "geometry" in e]
    near_lift = sum(1 for e in ways if Trail(e["id"], e.get("tags", {}), [(0, 0), (0, 0)]).style == "lift")
    print(f"overpass: {len(ways)} úseků, z toho {near_lift} jen s mtb:scale u lanovek; {lifts} lanovek", file=sys.stderr)
    if lifts and not near_lift:
        raise OverpassError(f"neúplná odpověď: {lifts} lanovek, ale žádné MTB cesty u nich")
    return data


def fetch_overpass(query: str, retries: int = 3, check=None, timeout: int = 960) -> dict:
    check = check or check_response
    data = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        for url in ENDPOINTS:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    return check(json.load(res))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OverpassError) as e:
                last = e
                print(f"overpass {url}: {e}", file=sys.stderr)
        time.sleep(5 * 2 ** attempt)  # 429 / 502 / 504 jsou u Overpass běžné
    raise RuntimeError(f"Overpass nedostupný: {last}")


# --- datový model -------------------------------------------------------------

@dataclass
class Trail:
    osm_id: int
    tags: dict
    coords: list[tuple[float, float]]  # (lat, lon)

    @property
    def length_m(self) -> float:
        return line_length_m(self.coords)

    @property
    def trail_name(self) -> str | None:
        return self.tags.get("name") or self.tags.get("mtb:name")

    @property
    def is_access(self) -> bool:
        """Lesní / přístupová cesta (highway=track bez názvu a MTB škál), ne lajna."""
        return (self.tags.get("highway") == "track" and not self.trail_name
                and "mtb:scale:imba" not in self.tags and "mtb:scale:dh" not in self.tags)

    @property
    def style(self) -> str:
        """dh = sjezdová, imba = singltrek / trailpark, lift = MTB cesta u lanovky, other"""
        if "mtb:scale:dh" in self.tags:
            return "dh"
        if "mtb:scale:imba" in self.tags:
            return "imba"
        if self.tags.get("mtb") == "designated":
            return "other"
        return "lift"  # jen s mtb:scale – do dat se dostane jen dotazem kolem lanovek


@dataclass
class Anchor:
    osm_type: str
    osm_id: int
    lat: float
    lon: float
    tags: dict

    @property
    def name(self) -> str | None:
        return self.tags.get("name")

    @property
    def url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"

    @property
    def website(self) -> str | None:
        return self.tags.get("website") or self.tags.get("contact:website") or self.tags.get("url")


@dataclass
class Cluster:
    id: str
    trails: list[Trail]
    anchors: list[Anchor] = field(default_factory=list)
    place: str | None = None  # nejbližší obec – pro shluky bez názvu

    @property
    def center(self) -> tuple[float, float]:
        """Těžiště bodů geometrie (lat, lon)."""
        pts = [p for t in self.trails for p in t.coords]
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)

    @property
    def length_m(self) -> float:
        return sum(t.length_m for t in self.trails)

    @property
    def named_trails(self) -> int:
        """Počet pojmenovaných lajn. Jedna lajna bývá v OSM rozdělená na víc cest (way),
        proto se počítají různé názvy, ne cesty."""
        return len({t.trail_name for t in self.trails if t.trail_name and not t.is_access})

    @property
    def kind(self) -> str:
        c = Counter(t.style for t in self.trails)
        downhill = c["dh"] + c["lift"]
        return "bikepark" if downhill and downhill >= c["imba"] else "trail_center"

    @property
    def name(self) -> str | None:
        named = [a for a in self.anchors if a.name]
        if named:
            # leisure=bike_park > sportoviště > MTB trasa > lanovka
            named.sort(key=lambda a: (a.tags.get("leisure") != "bike_park", "aerialway" in a.tags, a.tags.get("type") == "route"))
            return named[0].name
        return None

    @property
    def label(self) -> str:
        """Název pro report: název z OSM, jinak „u <obec>“."""
        return self.name or (f"u obce {self.place}" if self.place else "–")

    @property
    def website(self) -> str | None:
        return next((a.website for a in self.anchors if a.website), None)


@dataclass
class Place:
    name: str
    lat: float
    lon: float


def parse_overpass(data: dict) -> tuple[list[Trail], list[Anchor], list[Place]]:
    trails: dict[int, Trail] = {}
    anchors: dict[tuple[str, int], Anchor] = {}
    places: list[Place] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if "place" in tags and el["type"] == "node":
            if tags.get("name") and "lat" in el:
                places.append(Place(tags["name"], el["lat"], el["lon"]))
            continue
        if el["type"] == "way" and "geometry" in el:
            coords = [(p["lat"], p["lon"]) for p in el["geometry"] if p]
            if len(coords) >= 2:
                trails[el["id"]] = Trail(el["id"], tags, coords)
            continue
        c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
        if c:
            anchors[(el["type"], el["id"])] = Anchor(el["type"], el["id"], c["lat"], c["lon"], tags)
    return list(trails.values()), list(anchors.values()), places


# --- shlukování ---------------------------------------------------------------

def cluster_trails(trails: list[Trail], link_m: float = LINK_M) -> list[list[Trail]]:
    """Single-linkage: dvě lajny jsou ve stejném shluku, když se některé jejich body
    přiblíží na link_m. Body se třídí do mřížky, takže se nepočítá každý s každým."""
    parent = list(range(len(trails)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = link_m / 111_000  # stupně šířky; délku stačí přibližně (v ČR cos(50°) ≈ 0,64)
    grid: dict[tuple[int, int], list[tuple[int, float, float]]] = defaultdict(list)
    for i, t in enumerate(trails):
        for lat, lon in t.coords[:: max(1, len(t.coords) // 20)] + [t.coords[-1]]:
            grid[(int(lat / cell), int(lon / (cell / 0.64)))].append((i, lat, lon))

    for (gy, gx), pts in grid.items():
        neigh = [p for dy in (-1, 0, 1) for dx in (-1, 0, 1) for p in grid.get((gy + dy, gx + dx), ())]
        for i, lat, lon in pts:
            for j, lat2, lon2 in neigh:
                if find(i) != find(j) and haversine_m(lat, lon, lat2, lon2) <= link_m:
                    parent[find(i)] = find(j)

    groups: dict[int, list[Trail]] = defaultdict(list)
    for i, t in enumerate(trails):
        groups[find(i)].append(t)
    return list(groups.values())


def build_clusters(trails: list[Trail], anchors: list[Anchor], places: list[Place] | None = None) -> list[Cluster]:
    groups = [g for g in cluster_trails(trails)
              if len(g) >= MIN_TRAILS and sum(t.length_m for t in g) >= MIN_LENGTH_M]
    clusters = []
    for g in sorted(groups, key=lambda g: -sum(t.length_m for t in g)):
        c = Cluster(id=f"c{min(t.osm_id for t in g)}", trails=g)
        lat, lon = c.center
        c.anchors = [a for a in anchors if haversine_m(lat, lon, a.lat, a.lon) <= ANCHOR_RADIUS_M
                     or any(haversine_m(p[0], p[1], a.lat, a.lon) <= 300 for t in g for p in t.coords[::5])]
        if places:
            c.place = min(places, key=lambda pl: haversine_m(lat, lon, pl.lat, pl.lon)).name
        clusters.append(c)
    return clusters


# --- porovnání ----------------------------------------------------------------

@dataclass
class Match:
    park: dict                  # Feature z parks.geojson
    cluster: Cluster | None
    distance_m: float | None


def match_parks(features: list[dict], clusters: list[Cluster]) -> tuple[list[Match], list[Cluster]]:
    """Každému našemu parku najde nejlepší shluk do MATCH_RADIUS_M.

    Skóre = podobnost názvu + blízkost (0..1). Bez shody v názvu se páruje jen do NONAME_MATCH_M.
    Každý shluk lze použít jen jednou (greedy podle skóre).
    """
    scored = []
    for fi, f in enumerate(features):
        lon, lat = f["geometry"]["coordinates"][:2]
        names = [f["properties"].get("name"), f["properties"].get("town")]
        for ci, c in enumerate(clusters):
            clat, clon = c.center
            d = haversine_m(lat, lon, clat, clon)
            if d > MATCH_RADIUS_M:
                continue
            ns = max(name_score(n, c.name) for n in names)
            if ns == 0 and d > NONAME_MATCH_M:
                continue
            scored.append((ns + (1 - d / MATCH_RADIUS_M), fi, ci, d))

    scored.sort(reverse=True)
    used_f, used_c, best = set(), set(), {}
    for _, fi, ci, d in scored:
        if fi in used_f or ci in used_c:
            continue
        used_f.add(fi)
        used_c.add(ci)
        best[fi] = (ci, d)

    matches = [Match(f, clusters[best[fi][0]], best[fi][1]) if fi in best else Match(f, None, None)
               for fi, f in enumerate(features)]
    return matches, [c for ci, c in enumerate(clusters) if ci not in used_c]


# --- výstupy ------------------------------------------------------------------

KIND_LABEL = {"bikepark": "bikepark", "trail_center": "trailpark / singltrek"}


def _pt(lat: float, lon: float) -> str:
    return f"`[{lon:.5f}, {lat:.5f}]`"


def _osm_link(c: Cluster) -> str:
    lat, lon = c.center
    return f"[mapa](https://www.openstreetmap.org/#map=15/{lat:.5f}/{lon:.5f})"


def render_report(matches: list[Match], extra: list[Cluster], moved_threshold_m: float = 300) -> str:
    found = [m for m in matches if m.cluster]
    missing = [m for m in matches if not m.cluster]
    out = ["# OSM × parks.geojson", "",
           f"- parků v seznamu: **{len(matches)}**, se shlukem lajn v OSM: **{len(found)}**, "
           f"bez shluku: **{len(missing)}**, shluků navíc (kandidáti): **{len(extra)}**", ""]

    out += ["## Shody", "",
            "| id | název (my) | název (OSM) | odchylka | návrh [lon, lat] | pojm. lajn | úseků OSM | km | typ (OSM) | web (OSM) | |",
            "|---|---|---|---:|---|---:|---:|---:|---|---|---|"]
    for m in sorted(found, key=lambda m: -m.distance_m):
        p, c = m.park["properties"], m.cluster
        flag = " ⚠️" if m.distance_m > moved_threshold_m else ""
        out.append(f"| `{p['id']}` | {p['name']} | {c.label} | {m.distance_m:,.0f} m{flag} | {_pt(*c.center)} "
                   f"| {c.named_trails} | {len(c.trails)} | {c.length_m / 1000:.1f} | {KIND_LABEL[c.kind]} | {c.website or ''} | {_osm_link(c)} |")
    out += ["", "Úsek OSM = jedna cesta (way); lajna bývá rozdělená na víc úseků. "
                "„pojm. lajn“ = počet různých názvů, km včetně lesních cest u lanovek.",
            f"⚠️ = odchylka nad {moved_threshold_m:.0f} m. Střed shluku nemusí být nástup do parku – posuď v mapě.", ""]

    out += ["## Bez shluku v OSM", ""]
    out += [f"- `{m.park['properties']['id']}` {m.park['properties']['name']}" for m in missing] or ["- (žádné)"]
    out.append("")

    out += ["## Shluky, které v seznamu chybí (kandidáti)", "",
            "| název (OSM) | [lon, lat] | pojm. lajn | úseků OSM | km | typ | web | |", "|---|---|---:|---:|---:|---|---|---|"]
    for c in extra:
        out.append(f"| {c.label} | {_pt(*c.center)} | {c.named_trails} | {len(c.trails)} | {c.length_m / 1000:.1f} "
                   f"| {KIND_LABEL[c.kind]} | {c.website or ''} | {_osm_link(c)} |")
    out += ["", "Data © přispěvatelé OpenStreetMap, ODbL."]
    return "\n".join(out) + "\n"


def clusters_geojson(clusters: list[Cluster]) -> dict:
    feats = []
    for c in clusters:
        lat, lon = c.center
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {"cluster_id": c.id, "name": c.name, "place": c.place, "kind": c.kind, "website": c.website,
                           "segments": len(c.trails), "named_trails": c.named_trails, "length_m": round(c.length_m),
                           "anchors": [f"{a.osm_type}/{a.osm_id}" for a in c.anchors]},
        })
    return {"type": "FeatureCollection", "features": feats}


def trails_geojson(clusters: list[Cluster]) -> dict:
    keep = ("name", "mtb:scale", "mtb:scale:dh", "mtb:scale:imba", "mtb:name", "highway", "bicycle", "oneway:bicycle")
    feats = []
    for c in clusters:
        for t in c.trails:
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in t.coords]},
                "properties": {"cluster_id": c.id, "osm": f"way/{t.osm_id}", "length_m": round(t.length_m),
                               **{k: v for k, v in t.tags.items() if k in keep}},
            })
    return {"type": "FeatureCollection", "features": feats}


# --- lajny parků pro web (fáze 2) --------------------------------------------

DIFFICULTIES = ("green", "blue", "red", "black")


def _scale(value: str | None) -> int | None:
    m = re.match(r"\d", value or "")
    return int(m.group()) if m else None


def difficulty(tags: dict) -> str | None:
    """Obtížnost lajny v barvách českých trail center (green / blue / red / black).

    mtb:scale:imba 0–4 (IMBA) má přednost, pak mtb:scale 0–6 (STS), pak mtb:scale:dh."""
    imba = _scale(tags.get("mtb:scale:imba"))
    if imba is not None:
        return DIFFICULTIES[max(0, min(imba, 4) - 1)] if imba else "green"
    sts = _scale(tags.get("mtb:scale"))
    if sts is not None:
        return DIFFICULTIES[min(sts, 3)]
    dh = _scale(tags.get("mtb:scale:dh"))
    if dh is not None:
        return DIFFICULTIES[min(dh, 3)]
    return None


def simplify(coords: list[tuple[float, float]], min_m: float = 8) -> list[tuple[float, float]]:
    """Vynechá body bližší než min_m od předchozího ponechaného (menší soubor pro web)."""
    out = [coords[0]]
    for p in coords[1:-1]:
        if haversine_m(*out[-1], *p) >= min_m:
            out.append(p)
    out.append(coords[-1])
    return out


def park_trails_geojson(matches: list[Match]) -> dict:
    """Lajny shluků spárovaných s našimi parky – web/data/trails.geojson."""
    feats = []
    for m in matches:
        if not m.cluster:
            continue
        park_id = m.park["properties"]["id"]
        for t in sorted(m.cluster.trails, key=lambda t: t.osm_id):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in simplify(t.coords)]},
                "properties": {
                    "park_id": park_id,
                    "name": t.trail_name,
                    "access": t.is_access,
                    "difficulty": difficulty(t.tags),
                    "length_m": round(t.length_m),
                    "osm": f"way/{t.osm_id}",
                },
            })
    return {"type": "FeatureCollection", "attribution": "© přispěvatelé OpenStreetMap, ODbL", "features": feats}


# --- CLI ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parks", type=Path, default=PARKS, help="náš seznam parků (default: %(default)s)")
    ap.add_argument("--cache", type=Path, help="JSON odpověď Overpass: když existuje, použije se místo dotazu; jinak se do něj uloží")
    ap.add_argument("--report", help="Markdown report ('-' = stdout)")
    ap.add_argument("--geojson", type=Path, help="shluky jako GeoJSON body")
    ap.add_argument("--trails", type=Path, help="lajny všech shluků jako GeoJSON LineString")
    ap.add_argument("--park-trails", type=Path, help="lajny shluků spárovaných s našimi parky (web/data/trails.geojson)")
    args = ap.parse_args(argv)

    if args.cache and args.cache.exists():
        data = check_response(json.loads(args.cache.read_text()))
    else:
        data = fetch_overpass(QUERY)
        if args.cache:
            args.cache.write_text(json.dumps(data, ensure_ascii=False))

    trails, anchors, places = parse_overpass(data)
    clusters = build_clusters(trails, anchors, places)
    print(f"OSM: {len(trails)} úseků (way), {len(anchors)} kotev, {len(places)} obcí, {len(clusters)} shluků", file=sys.stderr)

    if args.geojson:
        args.geojson.write_text(json.dumps(clusters_geojson(clusters), ensure_ascii=False, indent=1) + "\n")
    if args.trails:
        args.trails.write_text(json.dumps(trails_geojson(clusters), ensure_ascii=False) + "\n")
    if args.report or args.park_trails:
        features = json.loads(args.parks.read_text())["features"]
        matches, extra = match_parks(features, clusters)
    if args.park_trails:
        pt = park_trails_geojson(matches)
        args.park_trails.write_text(json.dumps(pt, ensure_ascii=False, separators=(",", ":")) + "\n")
        print(f"trails.geojson: {len(pt['features'])} úseků, {len({f['properties']['park_id'] for f in pt['features']})} parků", file=sys.stderr)
    if args.report:
        report = render_report(matches, extra)
        if args.report == "-":
            sys.stdout.write(report)
        else:
            Path(args.report).write_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
