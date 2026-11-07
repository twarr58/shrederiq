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

--apply přepíše obtížnost spárovaných lajn barvou ze seznamu provozovatele (vlastnost
`difficulty_source: "official"`): značení parku je to, co jezdec vidí na místě, mtb:scale
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


GENERIC = {"trail", "trat", "lajna", "line", "trasa", "stezka", "okruh", "cesta", "singltrek", "singletrail", "flowtrail"}
COLORS = {"modr": "blue", "cerven": "red", "cern": "black", "zelen": "green", "blue": "blue", "red": "red",
          "black": "black", "green": "green"}


def tokens(s: str | None) -> set[str]:
    """Jméno → množina slov bez obecných slov (trail, trať, lajna…), barvy sjednocené
    („Modrý trail“ ~ „Modrá trať“ ~ blue)."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", fold(s)):
        if w in GENERIC:
            continue
        for stem, color in COLORS.items():
            if w.startswith(stem):
                w = color
                break
        out.add(w)
    return out


def same(a: str | None, b: str | None) -> bool:
    ta, tb = tokens(a), tokens(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


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


def match(official: list[dict], osm: dict[str, dict]) -> dict:
    """Vrací {"matched": [(off, name, osm_row)], "missing": [off], "extra": [name]}."""
    used, matched, missing = set(), [], []
    for off in official:
        # přesná shoda slov má přednost před podmnožinou („Koutíkův sen, Stará medvědice“ je
        # společný kousek dvou lajn, ne Stará Medvědice), při víc kandidátech ten nejdelší
        cands = [n for n in osm if n not in used and same(off["name"], n)]
        hit = max(cands, key=lambda n: (tokens(n) == tokens(off["name"]), osm[n]["length_m"]), default=None)
        if hit:
            used.add(hit)
            matched.append((off, hit, osm[hit]))
        else:
            missing.append(off)
    extra = [n for n in osm if n not in used]
    return {"matched": matched, "missing": missing, "extra": extra}


def compare(fc: dict, official_dir: Path = OFFICIAL_DIR) -> dict[str, dict]:
    by_park = osm_trails(fc)
    out = {}
    for path in sorted(official_dir.glob("*.json")):
        pid = path.stem
        data = json.loads(path.read_text())
        res = match(data.get("trails", []), by_park.get(pid, {}))
        diffs = []
        for off, name, row in res["matched"]:
            notes = []
            if off.get("difficulty") and row["difficulty"] and off["difficulty"] != row["difficulty"]:
                notes.append(f"obtížnost {off['difficulty']} vs OSM {row['difficulty']}")
            if off.get("length_m") and row["length_m"] and abs(row["length_m"] - off["length_m"]) > LENGTH_TOL * off["length_m"]:
                notes.append(f"délka {off['length_m']} m vs OSM {row['length_m']} m")
            if notes:
                diffs.append((off["name"], name, "; ".join(notes)))
        out[pid] = {"source": data.get("source"), "official": len(data.get("trails", [])), "osm": len(by_park.get(pid, {})),
                    "matched": len(res["matched"]), "missing": [o["name"] for o in res["missing"]],
                    "extra": res["extra"], "diffs": diffs}
    return out


def apply(fc: dict, official_dir: Path = OFFICIAL_DIR) -> int:
    """Obtížnost spárovaných lajn podle oficiálního seznamu; vrací počet změněných úseků."""
    by_park = osm_trails(fc)
    want: dict[tuple[str, str], str] = {}  # (park_id, fold(name)) → difficulty
    for path in sorted(official_dir.glob("*.json")):
        pid = path.stem
        data = json.loads(path.read_text())
        for off, name, _ in match(data.get("trails", []), by_park.get(pid, {}))["matched"]:
            if off.get("difficulty"):
                want[(pid, fold(name))] = off["difficulty"]
    changed = 0
    for f in fc.get("features", []):
        p = f["properties"]
        d = want.get((p.get("park_id"), fold(p.get("name"))))
        if d and not p.get("access"):
            if p.get("difficulty") != d:
                changed += 1
            p["difficulty"] = d
            p["difficulty_source"] = "official"
    return changed


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
    args = ap.parse_args(argv)
    fc = json.loads(args.trails.read_text())
    result = compare(fc, args.official)
    text = render(result)
    if args.report:
        args.report.write_text(text)
    print(text, file=sys.stderr)
    if args.apply:
        n = apply(fc, args.official)
        args.trails.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
        print(f"official: obtížnost podle značení parku u {n} úseků", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
