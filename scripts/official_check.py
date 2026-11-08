#!/usr/bin/env python3
"""Porovná lajny z OSM s oficiálními seznamy trailů provozovatelů (data/official/*.json), #22.

Pro každý park se souborem data/official/<park_id>.json:
  - spáruje oficiální traily s pojmenovanými lajnami z trails.geojson (jména bez diakritiky,
    bez obecných slov trail / trať / lajna a se sjednocenými barvami: „Modrá trať“ ~ „Modrý
    trail“; stačí, když slova jednoho jména jsou podmnožinou druhého),
  - vypíše traily, které v OSM chybí, lajny z OSM, které provozovatel neuvádí, a páry,
    kde se liší obtížnost nebo délka o víc než LENGTH_TOL.

  python3 scripts/official_check.py web/data/trails.geojson --report out/official.md
  python3 scripts/official_check.py web/data/trails.geojson --apply   # barvy z oficiálních seznamů do lajn

--apply přepíše jméno a obtížnost spárovaných lajn podle seznamu provozovatele (původní
jméno zůstane v `osm_name`, obtížnost s `difficulty_source: "official"`): značení parku je to, co jezdec vidí na místě, mtb:scale
v OSM je technická škála a často se liší.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

OFFICIAL_DIR = Path(__file__).resolve().parent.parent / "data" / "official"
LENGTH_TOL = 0.3


def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


GENERIC = {"trail", "trat", "lajna", "line", "trasa", "stezka", "okruh", "cesta", "singltrek", "singletrail", "flowtrail",
           # části jedné lajny v OSM („Spičák Trail Modrý horní část“)
           "horni", "dolni", "cast", "usek"}
COLORS = {"modr": "blue", "cerven": "red", "cern": "black", "zelen": "green", "blue": "blue", "red": "red",
          "black": "black", "green": "green"}
COLOR_TOKENS = set(COLORS.values())
PARK_WORDS = {"bikepark", "bike", "park", "trailpark", "resort", "areal", "ski", "skiareal"}


def tokens(s: str | None, drop: frozenset[str] = frozenset()) -> set[str]:
    """Jméno → množina slov bez obecných slov (trail, trať, lajna…), barvy sjednocené
    („Modrý trail“ ~ „Modrá trať“ ~ blue). drop = slova z názvu parku („TT“, „Spičák“)."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", fold(s)):
        if w in GENERIC or w in drop:
            continue
        for stem, color in COLORS.items():
            if w.startswith(stem):
                w = color
                break
        out.add(w)
    return out


def park_tokens(park_id: str, name: str | None = None) -> frozenset[str]:
    """Slova názvu parku, která OSM dává před jména lajn: id parku, slova názvu a zkratka
    (Trutnov Trails → tt, Tanvaldský Špičák → tanvaldsky, spicak)."""
    words = [w for w in re.findall(r"[a-z0-9]+", fold(name)) if w not in PARK_WORDS]
    out = set(park_id.split("-")) | set(words)
    if len(words) >= 2:
        out.add("".join(w[0] for w in words))
    return frozenset(w for w in out if w not in COLOR_TOKENS)


def _near(a: str, b: str) -> bool:
    """Překlep: stejně dlouhá slova (aspoň 5 znaků) s jedním jiným písmenem, nebo jedno písmeno navíc."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def score(a: str | None, b: str | None, drop: frozenset[str] = frozenset()) -> int:
    """Jak dobře si jména odpovídají: 3 stejná slova, 2 slova jednoho jsou podmnožinou
    druhého, 1 stejná slova až na překlep, 0 nic. Shoda jen v barvě je 1 (slabá)."""
    ta, tb = tokens(a, drop), tokens(b, drop)
    if not ta or not tb:
        return 0
    if ta == tb:
        return 3
    if ta <= tb or tb <= ta:
        return 1 if (ta & tb) <= COLOR_TOKENS else 2
    if len(ta) == len(tb) and all(any(_near(x, y) for y in tb) for x in ta):
        return 1
    return 0


def same(a: str | None, b: str | None) -> bool:
    return score(a, b) > 0


def osm_trails(fc: dict) -> dict[str, dict[str, dict]]:
    """park_id → název → {length_m, difficulty} (úseky stejného jména sečtené)."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for f in fc.get("features", []):
        p = f["properties"]
        if p.get("access") or not p.get("name"):
            continue
        # stejné jméno bez ohledu na velikost písmen („Stará Medvědice“ / „Stará medvědice“)
        key = fold(p["name"])
        row = out[p["park_id"]].setdefault(key, {"name": p["name"], "length_m": 0, "by": defaultdict(int)})
        row["length_m"] += p.get("length_m") or 0
        row["by"][p.get("difficulty") or ""] += p.get("length_m") or 0
    # klíč → zobrazované jméno (první pravopis), aby report tiskl jména z OSM
    named: dict[str, dict[str, dict]] = defaultdict(dict)
    for pid, rows in out.items():
        for row in rows.values():
            row["difficulty"] = max(row["by"].items(), key=lambda kv: kv[1])[0] or None
            del row["by"]
            named[pid][row.pop("name")] = row
    return named


def match(official: list[dict], osm: dict[str, dict], drop: frozenset[str] = frozenset()) -> dict:
    """Každé jméno z OSM k nejlépe odpovídajícímu oficiálnímu trailu (i alias); jeden trail
    může mít víc jmen v OSM („Kozí trail“ i „TT Kozí trail“). Jméno, které sedí stejně dobře
    na dva traily (společný úsek „Koutíkův sen, Stará medvědice“), zůstane nespárované.
    Slabá shoda (jen barva, překlep) platí, jen když je jednoznačná z obou stran.
    Vrací {"matched": [(off, [jména], součet)], "missing": [off], "extra": [jméno]}."""
    def best(off, name):
        # alias napsaný přesně jako jméno v OSM je výslovné přiřazení a vyhrává nad shodou slov
        # (Hrubá Voda: OSM „Jump line“ je červená jump lajna, ne černá „Jump lajna“)
        if any(fold(a) == fold(name) for a in off.get("aliases", [])):
            return 4
        return max(score(n, name, drop) for n in [off["name"], *off.get("aliases", [])])

    pairs = {}  # jméno z OSM → index oficiálního trailu
    for name in osm:
        scores = [best(off, name) for off in official]
        top = max(scores, default=0)
        winners = [i for i, sc in enumerate(scores) if sc == top]
        if top and len(winners) == 1:
            pairs[name] = (winners[0], top)
    # slabé shody jen jednoznačné: všechna slabě spárovaná jména trailu musí být po vypuštění
    # „horní / dolní část“ stejná (části jedné lajny), jinak je to hádání
    weak = defaultdict(set)
    for name, (i, sc) in pairs.items():
        if sc == 1:
            weak[i].add(frozenset(tokens(name, drop)))
    pairs = {n: i for n, (i, sc) in pairs.items() if sc > 1 or len(weak[i]) == 1}
    # přesná shoda jména (po vypuštění slov parku) má přednost: když ji trail má, podmnožiny
    # se k němu nepřidají (jsou to jiné lajny, např. „Mamutovka AIR“ vedle „Mamutovka“)
    matched, missing = [], []
    for i, off in enumerate(official):
        names = [n for n, j in pairs.items() if j == i]
        exact = [n for n in names if best(off, n) >= 3]
        if exact:
            names = exact
        if names:
            longest = max(names, key=lambda n: osm[n]["length_m"])
            row = {"length_m": sum(osm[n]["length_m"] for n in names), "difficulty": osm[longest]["difficulty"]}
            matched.append((off, names, row))
        else:
            missing.append(off)
    used = {n for _, names, _ in matched for n in names}
    extra = [n for n in osm if n not in used]
    return {"matched": matched, "missing": missing, "extra": extra}


def _park_names() -> dict[str, str]:
    path = OFFICIAL_DIR.parent.parent / "web" / "data" / "parks.geojson"
    try:
        return {f["properties"]["id"]: f["properties"].get("name") for f in json.loads(path.read_text())["features"]}
    except (OSError, ValueError, KeyError):
        return {}


def compare(fc: dict, official_dir: Path = OFFICIAL_DIR) -> dict[str, dict]:
    by_park = osm_trails(fc)
    names = _park_names()
    out = {}
    for path in sorted(official_dir.glob("*.json")):
        pid = path.stem
        data = json.loads(path.read_text())
        res = match(data.get("trails", []), by_park.get(pid, {}), park_tokens(pid, names.get(pid)))
        diffs = []
        for off, osm_names, row in res["matched"]:
            notes = []
            if off.get("difficulty") and row["difficulty"] and off["difficulty"] != row["difficulty"]:
                notes.append(f"obtížnost {off['difficulty']} vs OSM {row['difficulty']}")
            if off.get("length_m") and row["length_m"] and abs(row["length_m"] - off["length_m"]) > LENGTH_TOL * off["length_m"]:
                notes.append(f"délka {off['length_m']} m vs OSM {row['length_m']} m")
            if notes:
                diffs.append((off["name"], " + ".join(osm_names), "; ".join(notes)))
        out[pid] = {"source": data.get("source"), "official": len(data.get("trails", [])), "osm": len(by_park.get(pid, {})),
                    "matched": len(res["matched"]), "missing": [o["name"] for o in res["missing"]],
                    "extra": res["extra"], "diffs": diffs}
    return out


def apply(fc: dict, official_dir: Path = OFFICIAL_DIR) -> int:
    """Spárované lajny dostanou jméno a obtížnost podle oficiálního seznamu (původní jméno
    z OSM zůstane v osm_name). Vrací počet úseků, kterým se změnila obtížnost."""
    by_park = osm_trails(fc)
    names = _park_names()
    want: dict[tuple[str, str], dict] = {}  # (park_id, fold(jméno z OSM)) → oficiální trail
    for path in sorted(official_dir.glob("*.json")):
        pid = path.stem
        data = json.loads(path.read_text())
        for off, osm_names, _ in match(data.get("trails", []), by_park.get(pid, {}), park_tokens(pid, names.get(pid)))["matched"]:
            for n in osm_names:
                want[(pid, fold(n))] = off
    changed = 0
    for f in fc.get("features", []):
        p = f["properties"]
        off = want.get((p.get("park_id"), fold(p.get("name"))))
        if not off or p.get("access"):
            continue
        if p["name"] != off["name"]:
            p["osm_name"] = p["name"]
            p["name"] = off["name"]
        if off.get("difficulty"):
            if p.get("difficulty") != off["difficulty"]:
                changed += 1
            p["difficulty"] = off["difficulty"]
            p["difficulty_source"] = "official"
    return changed


def web_lists(fc: dict, official_dir: Path = OFFICIAL_DIR) -> dict[str, dict]:
    """Oficiální seznamy pro web: park_id → {source, trails: [{name, difficulty, length_m, mapped}]}.
    mapped = trail má v trails.geojson aspoň jeden úsek; ostatní web ukáže jako „není v mapě“."""
    by_park = osm_trails(fc)
    names = _park_names()
    out = {}
    for path in sorted(official_dir.glob("*.json")):
        pid = path.stem
        data = json.loads(path.read_text())
        trails = data.get("trails", [])
        res = match(trails, by_park.get(pid, {}), park_tokens(pid, names.get(pid)))
        mapped = {id(off) for off, _, _ in res["matched"]}
        out[pid] = {"source": data.get("source"), "trails": [
            {k: t[k] for k in ("name", "difficulty", "length_m") if t.get(k) is not None} | {"mapped": id(t) in mapped}
            for t in trails]}
    return out


def render(result: dict[str, dict]) -> str:
    out = ["# Lajny × oficiální seznamy trailů", ""]
    if not result:
        return "\n".join(out + ["Žádný soubor v data/official/.", ""])
    out += ["| park | oficiálně | v OSM | spárováno | chybí v OSM | navíc v OSM | rozdíly |", "|---|---:|---:|---:|---|---|---|"]
    for pid, r in result.items():
        diffs = "; ".join(f"{o}: {n}" for o, _, n in r["diffs"])
        out.append(f"| `{pid}` | {r['official']} | {r['osm']} | {r['matched']} | {', '.join(r['missing']) or '–'} | "
                   f"{', '.join(r['extra']) or '–'} | {diffs or '–'} |")
    out += ["", "Zdroj: weby provozovatelů (data/official/*.json). Délka se hlásí při rozdílu nad 30 %.", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trails", type=Path)
    ap.add_argument("--official", type=Path, default=OFFICIAL_DIR)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--apply", action="store_true", help="zapsat obtížnost z oficiálních seznamů do trails.geojson")
    ap.add_argument("--web", type=Path, help="oficiální seznamy pro web (web/data/official.json): co v mapě chybí")
    args = ap.parse_args(argv)
    fc = json.loads(args.trails.read_text())
    result = compare(fc, args.official)
    text = render(result)
    if args.report:
        args.report.write_text(text)
    if args.web:
        args.web.write_text(json.dumps(web_lists(fc, args.official), ensure_ascii=False, separators=(",", ":")) + "\n")
    print(text, file=sys.stderr)
    if args.apply:
        n = apply(fc, args.official)
        args.trails.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
        print(f"official: obtížnost podle značení parku u {n} úseků", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
