import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mapy_trails as mt  # noqa: E402

PARK = (12.83, 50.355)


def fake_get(calls):
    def get(path, params):
        calls.append((path, params))
        if path == "geocode":
            return {"items": [
                {"label": "Restaurace", "position": {"lon": 12.83, "lat": 50.355}, "bbox": [0, 0, 1, 1]},
                {"label": "MTB trail", "position": {"lon": 12.836, "lat": 50.3539}, "bbox": [12.8268, 50.3532, 12.836, 50.354]}]}
        s = [float(v) for v in params["start"].split(",")]
        e = [float(v) for v in params["end"].split(",")]
        return {"length": 700, "geometry": {"geometry": {"type": "LineString", "coordinates": [s, [12.83, 50.3536], e]}}}
    return get


class MapyTrails(unittest.TestCase):
    def run_add(self, specs, cache, get):
        fc = {"features": []}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.json").write_text(json.dumps({"trails": specs}))
            res = mt.add_trails(fc, {"p": PARK}, cache, get, Path(d))
        return fc, res

    def test_geocoded_trail_routes_from_start_to_far_corner(self):
        calls = []
        fc, (added, missing) = self.run_add([{"name": "Easy Ella"}], {}, fake_get(calls))
        self.assertEqual((added, missing), (1, 0))
        geo = calls[0][1]
        self.assertEqual(geo["query"], "Trail Easy Ella")
        route = calls[1][1]
        self.assertEqual(route["start"], "12.836,50.3539")
        self.assertEqual(route["end"], "12.8268,50.3532")   # roh obdélníku nejdál od začátku
        p = fc["features"][0]["properties"]
        self.assertEqual((p["park_id"], p["name"], p["source"], p["access"]), ("p", "Easy Ella", "mapy", False))
        self.assertGreater(p["length_m"], 0)

    def test_manual_start_end_and_via_skip_geocoding_and_use_cache(self):
        calls, cache = [], {}
        spec = {"name": "Daniela", "start": [12.838, 50.355], "end": [12.824, 50.349], "via": [[12.83, 50.357]]}
        self.run_add([spec], cache, fake_get(calls))
        self.assertEqual([c[0] for c in calls], ["routing/route"])
        self.assertEqual(calls[0][1]["waypoints"], "12.83,50.357")
        calls.clear()
        fc, (added, _) = self.run_add([spec], cache, None)   # bez klíče jen z cache
        self.assertEqual((added, calls), (1, []))

    def test_trail_not_found_is_skipped(self):
        def get(path, params):
            return {"items": []}
        fc, (added, missing) = self.run_add([{"name": "Saint Terese"}], {}, get)
        self.assertEqual((added, missing, fc["features"]), (0, 1, []))

    def test_rename_osm_trails(self):
        fc = {"features": [{"properties": {"park_id": "p", "name": "Flow Rider", "access": False}},
                           {"properties": {"park_id": "q", "name": "Flow Rider", "access": False}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.json").write_text(json.dumps({"rename": {"Flow Rider": "Black Wood"}, "trails": []}))
            self.assertEqual(mt.apply_renames(fc, Path(d)), 1)
        self.assertEqual([f["properties"]["name"] for f in fc["features"]], ["Black Wood", "Flow Rider"])
        self.assertEqual(fc["features"][0]["properties"]["osm_name"], "Flow Rider")
        fc = {"features": [{"properties": {"park_id": "p", "name": None, "osm": "way/1+way/2", "access": False}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.json").write_text(json.dumps({"rename": {"way/2": "Skill trail"}, "trails": []}))
            self.assertEqual(mt.apply_renames(fc, Path(d)), 1)
        self.assertEqual(fc["features"][0]["properties"]["name"], "Skill trail")

    def test_replace_drops_osm_trail_of_same_name(self):
        fc = {"features": [{"properties": {"park_id": "p", "name": "Rubín"}},
                           {"properties": {"park_id": "p", "name": "rubín "}},
                           {"properties": {"park_id": "p", "name": "Baron"}},
                           {"properties": {"park_id": "q", "name": "Rubín"}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.json").write_text(json.dumps({"trails": [{"name": "Rubín", "replace": True}]}))
            self.assertEqual(mt.drop_replaced(fc, Path(d)), 2)
        self.assertEqual([(f["properties"]["park_id"], f["properties"]["name"]) for f in fc["features"]], [("p", "Baron"), ("q", "Rubín")])

    def test_replacement_keeps_osm_difficulty(self):
        mt.REPLACED_DIFF.clear()
        fc = {"features": [{"properties": {"park_id": "p", "name": "Velbloud", "difficulty": "black", "length_m": 500}}]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.json").write_text(json.dumps({"trails": [{"name": "Velbloud", "replace": True, "start": [12.84, 50.35], "end": [12.83, 50.35]}]}))
            mt.drop_replaced(fc, Path(d))
            mt.add_trails(fc, {"p": PARK}, {}, fake_get([]), Path(d))
        self.assertEqual([f["properties"]["difficulty"] for f in fc["features"]], ["black"])

    def test_repo_files_name_parks_and_trails(self):
        parks = {f["properties"]["id"] for f in json.loads((mt.MAPY_DIR.parent.parent / "web/data/parks.geojson").read_text())["features"]}
        for path in mt.MAPY_DIR.glob("*.json"):
            self.assertIn(path.stem, parks, path)
            for t in json.loads(path.read_text())["trails"]:
                self.assertTrue(t["name"], path)
                for pt in [t.get("start"), t.get("end"), *t.get("via", [])]:
                    if pt:
                        self.assertTrue(12 < pt[0] < 19 and 48.5 < pt[1] < 51.1, (path, t["name"], pt))


if __name__ == "__main__":
    unittest.main()
