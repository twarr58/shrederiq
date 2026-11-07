#!/usr/bin/env python3
"""Data parků z OpenStreetMap pro web: lajny, základna (dolní stanice lanovky) a parkoviště.

Na rozdíl od fetch_osm_parks.py (hledá parky v celé ČR) se tady ptáme Overpass
přímo kolem každého parku z web/data/parks.geojson (RADIUS_M):

  lajny       – cesty s mtb:scale:imba / mtb:scale:dh / mtb=designated,
                pěšiny a cesty s mtb:scale do LIFT_TRAIL_M od lanovky (bikeparky),
                členové relací route=mtb (lajny trail center bývají zmapované jen
                jako relace; název lajny se vezme z relace). Dlouhé cyklotrasy
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
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm_parks import (  # noqa: E402
    PARKS, OverpassError, difficulty, fetch_overpass, haversine_m, simplify,
)

RADIUS_M = 3000
LIFT_TRAIL_M = 1500
PARKING_M = 700
MAX_ROUTE_KM = 25
LIFTS = "chair_lift|gondola|cable_car|mixed_lift|drag_lift|t-bar|j-bar|platter"
BIKE_LIFTS = {"chair_lift", "gondola", "cable_car", "mixed_lift"}  # kola vozí hlavně tyhle
TRAIL_NAME_RE = re.compile(r"singl|single|trail|flow|bike ?park|enduro|downhill|freeride|\bdh\b", re.I)
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
        f'  way({a})["highway"="path"]["mtb"="designated"];\n'
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


def trail_relations(osm: dict) -> dict[int, str | None]:
    """Way id → název relace pro členy „trailových“ relací route=mtb."""
    out: dict[int, str | None] = {}
    for rel in osm["rels"].values():
        tags, name = rel["tags"], rel["tags"].get("name")
        members = [osm["ways"][w] for w in rel["ways"] if w in osm["ways"]]
        km = sum(_length(w["coords"]) for w in members) / 1000
        trailish = bool(name and TRAIL_NAME_RE.search(name))
        if not trailish and (tags.get("network") in LONG_NETWORKS or km > MAX_ROUTE_KM):
            continue
        for w in rel["ways"]:
            out.setdefault(w, name)
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
        strong = ("mtb:scale:imba" in tags or tags.get("mtb") == "designated"
                  or (tags.get("bicycle") == "designated" and tags.get("highway") == "path" and "mtb:scale" in tags))
        near_lift = "mtb:scale" in tags and any(
            _dist_to_line(c, l) <= LIFT_TRAIL_M for l in lifts for c in coords[::4] + [coords[-1]])
        if in_rel or strong or near_lift:
            out[wid] = (w, rel_names.get(wid))
    return out


def choose_base(park_pt, trails, osm, has_lift=True):
    """Dolní stanice lanovky s nejvíc lajnami do 400 m, jinak parkoviště nejblíž lajnám.
    Park, který podle parks.geojson lanovku nemá (lift: false), se k lanovce nepřichytí."""
    pts = [c for w, _ in trails.values() for c in w["coords"][::3]]
    best, best_score = None, 0.0
    for lid, l in (osm["lifts"].items() if has_lift is not False else ()):
        if l["tags"].get("aerialway") not in BIKE_LIFTS:
            continue
        if _dist_to_line(park_pt, l["coords"]) > RADIUS_M:
            continue
        score = sum(1 for p in pts if _dist_to_line(p, l["coords"]) <= 400)
        if score > best_score:
            best, best_score = l, score
    if best:
        return {"base": best["coords"][0], "base_source": "lift", "lift": best["tags"].get("name")}
    if pts:
        cx = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        near = [(haversine_m(*cx, *pk["pos"]), pk) for pk in osm["parkings"].values()]
        near = [x for x in near if x[0] <= 1500]
        if near:
            pk = min(near, key=lambda x: x[0])[1]
            return {"base": pk["pos"], "base_source": "parking", "lift": None}
    return {"base": None, "base_source": None, "lift": None}


def choose_parking(base, osm):
    if not base:
        return None
    near = [(haversine_m(*base, *pk["pos"]), pk) for pk in osm["parkings"].values()]
    near = [x for x in near if x[0] <= PARKING_M]
    return min(near, key=lambda x: x[0])[1] if near else None


def build(parks: list[dict], osm: dict):
    rel_names = trail_relations(osm)
    pts = {f["properties"]["id"]: (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) for f in parks}
    has_lift = {f["properties"]["id"]: f["properties"].get("lift") for f in parks}

    # Každou cestu jen k jednomu parku – k tomu, jehož základně (nebo bodu, když základnu
    # nemá) je nejblíž. Naše body parků bývají mimo, proto se nejdřív hledá základna.
    per_park = {pid: select_trails(pt, osm, rel_names, has_lift=has_lift[pid]) for pid, pt in pts.items()}
    anchor = {pid: choose_base(pts[pid], per_park[pid], osm, has_lift[pid])["base"] or pts[pid] for pid in pts}
    owner: dict[int, str] = {}
    for pid, cands in per_park.items():
        for wid, (w, _) in cands.items():
            d = _dist_to_line(anchor[pid], w["coords"])
            if wid not in owner or d < _dist_to_line(anchor[owner[wid]], w["coords"]):
                owner[wid] = pid

    features, info, rows = [], {}, []
    for f in parks:
        pid = f["properties"]["id"]
        trails = {wid: v for wid, v in per_park[pid].items() if owner.get(wid) == pid}
        base = choose_base(pts[pid], trails, osm, has_lift[pid])
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
            access = (tags.get("highway") == "track" and not name
                      and "mtb:scale:imba" not in tags and wid not in rel_names)
            if name and not access:
                names.add(name)
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in simplify(w["coords"])]},
                "properties": {"park_id": pid, "name": name, "difficulty": difficulty(tags), "access": access,
                               "length_m": round(_length(w["coords"])), "osm": f"way/{wid}"},
            })
        moved = round(haversine_m(*pts[pid], *base["base"])) if base["base"] else None
        rows.append((pid, f["properties"]["name"], base["base_source"], base["lift"], moved,
                     parking["name"] if parking and parking["name"] else ("ano" if parking else "–"),
                     len(names), len(trails)))
    trails_fc = {"type": "FeatureCollection", "attribution": "© přispěvatelé OpenStreetMap, ODbL", "features": features}
    return trails_fc, info, rows


def render_summary(rows) -> str:
    out = ["# Parky × OSM (lajny, základna, parkoviště)", "",
           "| id | park | základna | lanovka | posun bodu | parkoviště | pojm. lajn | úseků OSM |",
           "|---|---|---|---|---:|---|---:|---:|"]
    for pid, name, src, lift, moved, parking, named, segs in rows:
        src_l = {"lift": "lanovka", "parking": "parkoviště", None: "–"}[src]
        out.append(f"| `{pid}` | {name} | {src_l} | {lift or '–'} | {f'{moved:,} m' if moved is not None else '–'} "
                   f"| {parking} | {named} | {segs} |")
    out += ["", "Data © přispěvatelé OpenStreetMap, ODbL."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parks", type=Path, default=PARKS)
    ap.add_argument("--cache", type=Path, help="odpověď Overpass: když existuje, použije se; jinak se do ní uloží")
    ap.add_argument("--trails", type=Path)
    ap.add_argument("--info", type=Path)
    ap.add_argument("--summary", help="Markdown souhrn ('-' = stdout)")
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
