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


if __name__ == "__main__":
    unittest.main()
