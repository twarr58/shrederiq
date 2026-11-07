import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gps_check as gc  # noqa: E402

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.0" creator="OpenStreetMap.org" xmlns="http://www.topografix.com/GPX/1/0">
  <trk><trkseg>{pts}</trkseg></trk>
</gpx>"""


def line(pid, name, lat0=50.0, lon=14.6, n=9):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat0 + i * 0.0005] for i in range(n)]},
            "properties": {"park_id": pid, "name": name}}


class GpsCheck(unittest.TestCase):
    def test_parse_trackpoints(self):
        pts = gc.parse_trackpoints(GPX.format(pts='<trkpt lat="50.0" lon="14.6"/><trkpt lat="50.001" lon="14.601"><ele>5</ele></trkpt>'))
        self.assertEqual(pts, [(14.6, 50.0), (14.601, 50.001)])

    def test_coverage_and_report(self):
        ridden = line("p", "Flow")                       # stopy podél celé lajny
        empty = line("p", "Ghost", lon=14.62)            # bez stop
        trace = "".join(f'<trkpt lat="{50.0 + i * 0.0001:.5f}" lon="14.60003"/>' for i in range(41))
        calls = []

        def fetch(bbox, page):
            calls.append((bbox, page))
            return GPX.format(pts=trace)
        fc = {"features": [ridden, empty, {**line("p", "road"), "properties": {"park_id": "p", "access": True}}]}
        cache = {}
        summary = gc.check(fc, cache, fetch)
        self.assertEqual(ridden["properties"]["gps_cov"], 100)
        self.assertEqual(empty["properties"]["gps_cov"], 0)
        self.assertEqual(summary["p"]["low"], ["Ghost"])
        self.assertEqual(summary["p"]["trails"], 2)        # lesní cesta se nehodnotí
        self.assertEqual(len(calls), 1)                   # jedna stránka pod 5 000 bodů
        self.assertEqual(len(cache), 1)
        gc.check(fc, cache, fetch)
        self.assertEqual(len(calls), 1)                   # podruhé z cache
        text = gc.render(summary)
        self.assertIn("| `p` | 41 | 2 |", text)
        self.assertIn("Ghost", text)

    def test_fetch_error_is_reported_not_raised(self):
        def boom(bbox, page):
            raise RuntimeError("HTTP 509")
        summary = gc.check({"features": [line("p", "Flow")]}, {}, boom)
        self.assertIn("error", summary["p"])
        self.assertIn("chyba", gc.render(summary))

    def test_bbox(self):
        self.assertEqual(gc.bbox_of([line("p", "a")]), (14.597, 49.997, 14.603, 50.007))


if __name__ == "__main__":
    unittest.main()
