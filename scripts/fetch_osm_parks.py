#!/usr/bin/env python3
"""Stáhne bikeparky v ČR z OpenStreetMap (Overpass API) a porovná je s web/data/parks.geojson.

Hledá:
  - leisure=bike_park (bod / plocha / relace) – samotné bikeparky,
  - cesty s mtb:scale / mtb:scale:dh v okruhu kolem nich – hrubý počet lajn.

Výstup:
  - --geojson: OSM bikeparky jako GeoJSON body (pro ruční kontrolu / budoucí import),
  - --report:  Markdown report – shody s naším seznamem (vzdálenost, návrh polohy),
               naše parky bez protějšku v OSM a OSM parky, které v seznamu chybí.

Skript jen navrhuje, parks.geojson neupravuje. Bez závislostí (jen stdlib).

Příklady:
  python3 scripts/fetch_osm_parks.py --report osm-report.md
  python3 scripts/fetch_osm_parks.py --cache osm.json --report -      # uloží / použije odpověď Overpass
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
from dataclasses import dataclass, field
from pathlib import Path

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
]
USER_AGENT = "shrederiq/0.1 (+https://github.com/twarr58/shrederiq)"
ROOT = Path(__file__).resolve().parent.parent
PARKS = ROOT / "web" / "data" / "parks.geojson"

# okruh pro počítání MTB cest kolem bikeparku a pro párování s naším seznamem
TRAIL_RADIUS_M = 1500
MATCH_RADIUS_M = 5000

QUERY = f"""
[out:json][timeout:180];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
nwr["leisure"="bike_park"](area.cz)->.bp;
.bp out tags center;
way(around.bp:{TRAIL_RADIUS_M})["highway"~"^(path|track|cycleway|footway|bridleway)$"]["mtb:scale"];
way(around.bp:{TRAIL_RADIUS_M})["mtb:scale:dh"];
out tags center;
"""


@dataclass
class OsmPark:
    osm_type: str
    osm_id: int
    lat: float
    lon: float
    name: str | None
    tags: dict
    trails: int = 0

    @property
    def url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"


@dataclass
class Match:
    park: dict                     # Feature z parks.geojson
    osm: OsmPark | None
    distance_m: float | None
    candidates: list = field(default_factory=list)


# --- obecné pomůcky -----------------------------------------------------------

def fold(s: str | None) -> str:
    """'Bikepark Špičák' → 'bikepark spicak'"""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


STOPWORDS = {"bikepark", "bike", "park", "trail", "trailpark", "trails", "singltrek", "singletrack",
             "centrum", "center", "mtb", "areal", "pod", "nad", "na", "v", "u"}


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


# --- Overpass -----------------------------------------------------------------

def fetch_overpass(query: str, retries: int = 3) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        for url in ENDPOINTS:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=240) as res:
                    return json.load(res)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
                print(f"overpass {url}: {e}", file=sys.stderr)
        time.sleep(5 * 2 ** attempt)  # 429 / 504 jsou u Overpass běžné
    raise RuntimeError(f"Overpass nedostupný: {last}")


def parse_overpass(data: dict) -> list[OsmPark]:
    """Rozdělí odpověď na bikeparky a MTB cesty; cesty přiřadí nejbližšímu parku v okruhu."""
    parks: list[OsmPark] = []
    trails: dict[int, tuple[float, float]] = {}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c.get("lat") is None:
            continue
        if tags.get("leisure") == "bike_park":
            parks.append(OsmPark(el["type"], el["id"], c["lat"], c["lon"], tags.get("name"), tags))
        elif el["type"] == "way":
            trails[el["id"]] = (c["lat"], c["lon"])  # dedup: cesta může přijít z obou dotazů

    for lat, lon in trails.values():
        near = [(haversine_m(lat, lon, p.lat, p.lon), p) for p in parks]
        near = [x for x in near if x[0] <= TRAIL_RADIUS_M]
        if near:
            min(near, key=lambda x: x[0])[1].trails += 1
    return parks


# --- porovnání ----------------------------------------------------------------

def match_parks(features: list[dict], osm: list[OsmPark]) -> tuple[list[Match], list[OsmPark]]:
    """Každému našemu parku najde nejlepší OSM protějšek v okruhu MATCH_RADIUS_M.

    Skóre = podobnost názvu (0..1) + blízkost (0..1); bez shody v názvu se bere jen to,
    co je do 1,5 km. Každý OSM park lze použít jen jednou (greedy podle skóre).
    """
    scored = []
    for fi, f in enumerate(features):
        lon, lat = f["geometry"]["coordinates"][:2]
        names = [f["properties"].get("name"), f["properties"].get("town")]
        for oi, o in enumerate(osm):
            d = haversine_m(lat, lon, o.lat, o.lon)
            if d > MATCH_RADIUS_M:
                continue
            ns = max(name_score(n, o.name) for n in names)
            if ns == 0 and d > 1500:
                continue
            scored.append((ns + (1 - d / MATCH_RADIUS_M), fi, oi, d))

    scored.sort(reverse=True)
    used_f, used_o, best = set(), set(), {}
    for score, fi, oi, d in scored:
        if fi in used_f or oi in used_o:
            continue
        used_f.add(fi)
        used_o.add(oi)
        best[fi] = (oi, d)

    matches = []
    for fi, f in enumerate(features):
        if fi in best:
            oi, d = best[fi]
            matches.append(Match(f, osm[oi], d))
        else:
            matches.append(Match(f, None, None))
    unmatched = [o for oi, o in enumerate(osm) if oi not in used_o]
    return matches, unmatched


def to_geojson(osm: list[OsmPark]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(o.lon, 5), round(o.lat, 5)]},
                "properties": {
                    "osm": f"{o.osm_type}/{o.osm_id}",
                    "name": o.name,
                    "website": o.tags.get("website") or o.tags.get("contact:website"),
                    "mtb_trails_nearby": o.trails,
                },
            }
            for o in sorted(osm, key=lambda o: (o.name is None, fold(o.name), o.osm_id))
        ],
    }


def render_report(matches: list[Match], unmatched: list[OsmPark], moved_threshold_m: float = 300) -> str:
    out = ["# OSM × parks.geojson", ""]
    found = [m for m in matches if m.osm]
    missing = [m for m in matches if not m.osm]
    out.append(f"- parků v seznamu: **{len(matches)}**, nalezeno v OSM: **{len(found)}**, "
               f"bez protějšku: **{len(missing)}**, v OSM navíc: **{len(unmatched)}**")
    out.append("")

    out += ["## Shody", "",
            "| id | název (my) | název (OSM) | odchylka | návrh [lon, lat] | MTB cest v okolí | OSM |",
            "|---|---|---|---:|---|---:|---|"]
    for m in sorted(found, key=lambda m: -(m.distance_m or 0)):
        p, o = m.park["properties"], m.osm
        flag = " ⚠️" if m.distance_m > moved_threshold_m else ""
        out.append(f"| `{p['id']}` | {p['name']} | {o.name or '–'} | {m.distance_m:,.0f} m{flag} "
                   f"| `[{o.lon:.5f}, {o.lat:.5f}]` | {o.trails} | [{o.osm_type}/{o.osm_id}]({o.url}) |")
    out += ["", f"⚠️ = odchylka nad {moved_threshold_m:.0f} m – zvaž opravu polohy.", ""]

    out += ["## Bez protějšku v OSM", ""]
    out += [f"- `{m.park['properties']['id']}` {m.park['properties']['name']}" for m in missing] or ["- (žádné)"]
    out.append("")

    out += ["## V OSM navíc (kandidáti do seznamu)", "",
            "| název | [lon, lat] | MTB cest v okolí | web | OSM |", "|---|---|---:|---|---|"]
    for o in sorted(unmatched, key=lambda o: (-o.trails, fold(o.name))):
        web = o.tags.get("website") or o.tags.get("contact:website") or ""
        out.append(f"| {o.name or '–'} | `[{o.lon:.5f}, {o.lat:.5f}]` | {o.trails} | {web} | [{o.osm_type}/{o.osm_id}]({o.url}) |")
    out.append("")
    out.append("Data © přispěvatelé OpenStreetMap, ODbL.")
    return "\n".join(out) + "\n"


# --- CLI ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parks", type=Path, default=PARKS, help="náš seznam parků (default: %(default)s)")
    ap.add_argument("--cache", type=Path, help="JSON odpověď Overpass: když existuje, použije se místo dotazu; jinak se do něj uloží")
    ap.add_argument("--geojson", type=Path, help="kam uložit OSM bikeparky jako GeoJSON")
    ap.add_argument("--report", help="kam uložit Markdown report ('-' = stdout)")
    args = ap.parse_args(argv)

    if args.cache and args.cache.exists():
        data = json.loads(args.cache.read_text())
    else:
        data = fetch_overpass(QUERY)
        if args.cache:
            args.cache.write_text(json.dumps(data, ensure_ascii=False))

    osm = parse_overpass(data)
    print(f"OSM: {len(osm)} bikeparků (leisure=bike_park)", file=sys.stderr)

    if args.geojson:
        args.geojson.write_text(json.dumps(to_geojson(osm), ensure_ascii=False, indent=1) + "\n")

    if args.report:
        features = json.loads(args.parks.read_text())["features"]
        report = render_report(*match_parks(features, osm))
        if args.report == "-":
            sys.stdout.write(report)
        else:
            Path(args.report).write_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
