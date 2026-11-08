import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import official_check as oc  # noqa: E402


def feat(pid, name, diff, length, access=False):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[17, 50], [17.001, 50]]},
            "properties": {"park_id": pid, "name": name, "difficulty": diff, "length_m": length, "access": access}}


class Official(unittest.TestCase):
    def test_compare(self):
        fc = {"features": [feat("k", "Modrý Flowčko", "blue", 1500), feat("k", "Modrý Flowčko", "green", 900),
                           feat("k", "Fun Flow", "black", 1700), feat("k", "Lesní cesta", None, 500, access=True),
                           feat("k", "Neznámá", "red", 300), feat("x", "Jiný park", "red", 100)]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "k.json").write_text(json.dumps({"source": "https://x", "trails": [
                {"name": "Flowčko", "difficulty": "blue", "length_m": 2500},
                {"name": "Fun Flow", "difficulty": "red", "length_m": 1700},
                {"name": "Vlaštovka", "difficulty": "blue", "length_m": 2400}]}))
            res = oc.compare(fc, Path(d))
        r = res["k"]
        self.assertEqual((r["official"], r["osm"], r["matched"]), (3, 3, 2))
        self.assertEqual(r["missing"], ["Vlaštovka"])
        self.assertEqual(r["extra"], ["Neznámá"])
        self.assertEqual([d[0] for d in r["diffs"]], ["Fun Flow"])        # Flowčko 2400 vs 2500 je v toleranci
        self.assertIn("obtížnost red vs OSM black", r["diffs"][0][2])
        text = oc.render(res)
        self.assertIn("| `k` | 3 | 3 | 2 | Vlaštovka | Neznámá |", text)
        self.assertIn("Žádný soubor", oc.render({}))

    def test_same_name_different_case_is_one_trail(self):
        fc = {"features": [
            {"properties": {"park_id": "k", "name": "Stará Medvědice", "difficulty": "red", "length_m": 5}},
            {"properties": {"park_id": "k", "name": "Stará medvědice", "difficulty": "red", "length_m": 3995}}]}
        rows = oc.osm_trails(fc)["k"]
        self.assertEqual(list(rows), ["Stará Medvědice"])
        self.assertEqual(rows["Stará Medvědice"]["length_m"], 4000)

    def test_exact_name_beats_shared_segment(self):
        osm = {"Koutíkův sen, Stará medvědice": {"length_m": 5, "difficulty": "red"},
               "Stará medvědice": {"length_m": 4000, "difficulty": "red"},
               "Koutíkův sen": {"length_m": 3000, "difficulty": "black"}}
        res = oc.match([{"name": "Stará Medvědice"}, {"name": "Koutíkův sen"}], osm)
        self.assertEqual([m[1] for m in res["matched"]], [["Stará medvědice"], ["Koutíkův sen"]])
        self.assertEqual(res["extra"], ["Koutíkův sen, Stará medvědice"])

    def test_apply_sets_official_difficulty(self):
        fc = {"features": [
            {"properties": {"park_id": "k", "name": "Koutíkův sen", "difficulty": "red", "length_m": 3000}},
            {"properties": {"park_id": "k", "name": "Koutíkův sen", "difficulty": "red", "length_m": 10}},
            {"properties": {"park_id": "k", "name": "Zevl Line", "difficulty": "blue", "length_m": 500}},
            {"properties": {"park_id": "k", "name": "Koutíkův sen", "difficulty": "red", "length_m": 90, "access": True}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "k.json").write_text(json.dumps({"trails": [{"name": "Koutíkův sen", "difficulty": "black"}, {"name": "Zevl Line"}]}))
            self.assertEqual(oc.apply(fc, Path(d)), 2)
        ps = [f["properties"] for f in fc["features"]]
        self.assertEqual([p["difficulty"] for p in ps], ["black", "black", "blue", "red"])
        self.assertEqual(ps[0]["difficulty_source"], "official")
        self.assertNotIn("difficulty_source", ps[2])  # bez barvy v seznamu zůstává OSM

    def test_park_prefix_typo_alias_and_parts(self):
        osm = {"Kozí trail": {"length_m": 3000, "difficulty": "blue"},
               "TT Kozí trail": {"length_m": 1900, "difficulty": "blue"},          # předpona parku
               "Rýbrcour": {"length_m": 800, "difficulty": "red"},                 # překlep v OSM
               "TT Skratka k trailům": {"length_m": 300, "difficulty": None},       # předpona + překlep
               "Spičák Trail Modrý horní část": {"length_m": 900, "difficulty": "blue"},
               "Spičák Trail Modrý dolní část": {"length_m": 700, "difficulty": "blue"},
               "iXS Downhill": {"length_m": 2900, "difficulty": "black"},
               "Schody": {"length_m": 50, "difficulty": None}}
        official = [{"name": "Kozí trail", "length_m": 5100}, {"name": "Rýbrcoul"}, {"name": "Zkratka k trailům"},
                    {"name": "Modrý flow trail"}, {"name": "DHK", "aliases": ["iXS Downhill"]}]
        drop = oc.park_tokens("trutnov-trails", "Trutnov Trails") | oc.park_tokens("tanvaldsky-spicak", "Tanvaldský Špičák")
        res = oc.match(official, osm, drop)
        got = {off["name"]: sorted(names) for off, names, _ in res["matched"]}
        self.assertEqual(got, {"Kozí trail": ["Kozí trail", "TT Kozí trail"], "Rýbrcoul": ["Rýbrcour"],
                               "Zkratka k trailům": ["TT Skratka k trailům"],
                               "Modrý flow trail": ["Spičák Trail Modrý dolní část", "Spičák Trail Modrý horní část"],
                               "DHK": ["iXS Downhill"]})
        self.assertEqual(res["matched"][0][2]["length_m"], 4900)
        self.assertEqual(res["extra"], ["Schody"])

    def test_exact_alias_beats_name_match(self):
        osm = {"Jump line": {"length_m": 1588, "difficulty": "red"}}
        res = oc.match([{"name": "Červená jump lajna", "aliases": ["Jump line"]}, {"name": "Jump lajna"}], osm)
        self.assertEqual([(o["name"], n) for o, n, _ in res["matched"]], [("Červená jump lajna", ["Jump line"])])

    def test_color_only_match_must_be_unique(self):
        osm = {"Modrý A": {"length_m": 1, "difficulty": None}, "Modrý B": {"length_m": 1, "difficulty": None}}
        self.assertEqual(oc.match([{"name": "Modrá"}], osm)["matched"], [])

    def test_apply_renames_to_official_name(self):
        fc = {"features": [{"properties": {"park_id": "trutnov-trails", "name": "TT Kozí trail", "difficulty": "red", "length_m": 100}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "trutnov-trails.json").write_text(json.dumps({"trails": [{"name": "Kozí trail", "difficulty": "blue"}]}))
            oc.apply(fc, Path(d))
        p = fc["features"][0]["properties"]
        self.assertEqual((p["name"], p["osm_name"], p["difficulty"]), ("Kozí trail", "TT Kozí trail", "blue"))

    def test_web_lists_mark_missing(self):
        fc = {"features": [feat("k", "Fun Flow", "red", 1700)]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "k.json").write_text(json.dumps({"source": "https://x", "trails": [
                {"name": "Fun Flow", "difficulty": "red"}, {"name": "Vlaštovka", "difficulty": "blue", "length_m": 2400}]}))
            out = oc.web_lists(fc, Path(d))
        self.assertEqual(out["k"], {"source": "https://x", "trails": [
            {"name": "Fun Flow", "difficulty": "red", "mapped": True},
            {"name": "Vlaštovka", "difficulty": "blue", "length_m": 2400, "mapped": False}]})

    def test_fold_and_same(self):
        self.assertEqual(oc.fold("Modrý Flowčko "), "modry flowcko")
        self.assertTrue(oc.same("Modrá trať", "Modrý trail"))
        self.assertTrue(oc.same("Flowčko", "#2 Modrý Flowčko"))
        self.assertTrue(oc.same("Červená jump lajna", "Jump line"))
        self.assertFalse(oc.same("Modrá trať", "Červený trail"))
        self.assertFalse(oc.same("trail", "lajna"))   # jen obecná slova = žádná shoda

    def test_repo_official_files_are_valid(self):
        parks = {f["properties"]["id"] for f in json.loads((oc.OFFICIAL_DIR.parent.parent / "web/data/parks.geojson").read_text())["features"]}
        for path in oc.OFFICIAL_DIR.glob("*.json"):
            data = json.loads(path.read_text())
            self.assertIn(path.stem, parks, path)
            for t in data["trails"]:
                self.assertTrue(t["name"], path)
                self.assertIn(t.get("difficulty"), (None, "green", "blue", "red", "black"), path)
                self.assertIsInstance(t.get("aliases", []), list, path)


if __name__ == "__main__":
    unittest.main()
