import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gpx_trails as gt  # noqa: E402
import trailforks_trails as tf  # noqa: E402

# Google polyline z dokumentace: (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)
POLY = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


class Trailforks(unittest.TestCase):
    def test_decode_polyline(self):
        self.assertEqual(tf.decode_polyline(POLY), [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])

    def test_trail_feature_difficulty_and_access(self):
        base = {"trailid": 7, "title": "Flow", "track": {"encodedPath": POLY}}
        self.assertEqual(tf.trail_feature({**base, "difficulty": "4"}, "p")["properties"]["difficulty"], "blue")
        self.assertEqual(tf.trail_feature({**base, "difficulty": 5}, "p")["properties"]["difficulty"], "red")
        self.assertEqual(tf.trail_feature({**base, "difficulty": 8}, "p")["properties"]["difficulty"], "black")
        road = tf.trail_feature({**base, "difficulty": 1}, "p")["properties"]
        self.assertEqual((road["access"], road["difficulty"]), (True, None))
        self.assertIsNone(tf.trail_feature({**base, "difficulty": 11}, "p"))          # lanovka
        self.assertIsNone(tf.trail_feature({"difficulty": 4, "track": {}}, "p"))       # bez geometrie
        f = tf.trail_feature({**base, "difficulty": 3}, "p")
        self.assertEqual(f["properties"]["source"], "trailforks")
        self.assertEqual(f["properties"]["trailforks_id"], 7)
        self.assertGreater(f["properties"]["length_m"], 100_000)
        self.assertEqual(f["geometry"]["coordinates"][0], [-120.2, 38.5])  # [lon, lat]

    def test_path_list_fallback(self):
        f = tf.trail_feature({"difficulty": 4, "track": {"path": [[49.5, 14.6], [49.51, 14.61]]}}, "p")
        self.assertEqual(len(f["geometry"]["coordinates"]), 2)

    def test_merge_keeps_other_sources(self):
        osm = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"source": "osm"}}]}
        gpx = [{"type": "Feature", "properties": {"source": "gpx", "file": "p/a.gpx"}}]
        tfk = [{"type": "Feature", "properties": {"source": "trailforks", "trailforks_id": 7}}]
        out = gt.merge(gt.merge(osm, gpx), tfk, replace_key="trailforks_id")
        self.assertEqual([f["properties"]["source"] for f in out["features"]], ["osm", "gpx", "trailforks"])
        out = gt.merge(gt.merge(out, tfk, replace_key="trailforks_id"), gpx)   # opakování nic neduplikuje
        self.assertEqual([f["properties"]["source"] for f in out["features"]], ["osm", "trailforks", "gpx"])
        self.assertIn("Trailforks", out["attribution_gpx"])
        self.assertNotIn("attribution_gpx", gt.merge(osm, gpx))

    def test_main_without_key_or_rid(self):
        with tempfile.TemporaryDirectory() as d:
            parks = Path(d) / "parks.geojson"
            parks.write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [14.6, 49.5]},
                 "properties": {"id": "p", "trailforks_rid": 123}}]}))
            import os
            os.environ.pop("TRAILFORKS_APP_ID", None)
            self.assertEqual(tf.main(["--parks", str(parks), "--merge", str(Path(d) / "t.geojson")]), 0)
            self.assertFalse((Path(d) / "t.geojson").exists())


if __name__ == "__main__":
    unittest.main()
