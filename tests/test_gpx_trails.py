import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gpx_trails as gt  # noqa: E402

TRAILFORKS = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Trailforks" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Flow Line</name><type>blue</type>
    <trkseg>
      <trkpt lat="49.5350" lon="14.6000"><ele>700</ele></trkpt>
      <trkpt lat="49.5340" lon="14.6010"><ele>690</ele></trkpt>
      <trkpt lat="49.5330" lon="14.6020"><ele>680</ele></trkpt>
    </trkseg>
  </trk>
  <trk><name>DH</name><desc>Black diamond, roots and rocks</desc>
    <trkseg>
      <trkpt lat="49.5350" lon="14.5990"/><trkpt lat="49.5338" lon="14.5985"/>
    </trkseg>
  </trk>
</gpx>
"""

PLAIN = """<gpx version="1.1" creator="handmade">
  <trk><trkseg><trkpt lat="49.5300" lon="14.6100"/><trkpt lat="49.5290" lon="14.6110"/></trkseg></trk>
</gpx>
"""


class Gpx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "moninec").mkdir()
        (root / "moninec" / "trailforks.gpx").write_text(TRAILFORKS)
        (root / "moninec" / "red__Kamenná.gpx").write_text(PLAIN)
        (root / "neznamy").mkdir()
        (root / "neznamy" / "x.gpx").write_text(PLAIN)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_trailforks(self):
        feats = gt.parse_gpx(self.root / "moninec" / "trailforks.gpx", "moninec")
        self.assertEqual([f["properties"]["name"] for f in feats], ["Flow Line", "DH"])
        self.assertEqual([f["properties"]["difficulty"] for f in feats], ["blue", "black"])
        self.assertEqual({f["properties"]["source"] for f in feats}, {"trailforks"})
        self.assertEqual(feats[0]["geometry"]["coordinates"][0], [14.6, 49.535])
        self.assertGreater(feats[0]["properties"]["length_m"], 250)
        self.assertIs(feats[0]["properties"]["access"], False)

    def test_prefix_difficulty_and_name_from_file(self):
        (f,) = gt.parse_gpx(self.root / "moninec" / "red__Kamenná.gpx", "moninec")
        self.assertEqual((f["properties"]["name"], f["properties"]["difficulty"], f["properties"]["source"]), ("Kamenná", "red", "gpx"))

    def test_load_all_skips_unknown_parks(self):
        feats = gt.load_all(self.root, {"moninec"})
        self.assertEqual(len(feats), 3)
        self.assertEqual(len(gt.load_all(self.root)), 4)
        self.assertEqual(gt.load_all(self.root / "nope"), [])

    def test_merge_is_idempotent(self):
        osm = {"type": "FeatureCollection", "attribution": "OSM", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": {"park_id": "kouty", "name": "x", "osm": "way/1"}}]}
        gpx = gt.load_all(self.root, {"moninec"})
        merged = gt.merge(osm, gpx)
        merged = gt.merge(merged, gpx)
        self.assertEqual(len(merged["features"]), 4)
        self.assertEqual(merged["attribution"], "OSM")
        self.assertIn("Trailforks", merged["attribution_gpx"])

    def test_cli_merge_creates_file(self):
        out = self.root / "trails.geojson"
        parks = self.root / "parks.geojson"
        parks.write_text(json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [14.6, 49.535]}, "properties": {"id": "moninec"}}]}))
        gt.main(["--gpx-dir", str(self.root), "--parks", str(parks), "--merge", str(out)])
        data = json.loads(out.read_text())
        self.assertEqual(len(data["features"]), 3)


if __name__ == "__main__":
    unittest.main()
