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


    def test_fit_moves_line_onto_traces(self):
        # lajna v OSM 30 m vedle, stopy hustě podél skutečné trasy
        drawn = line("p", "Korida", lon=14.6004)
        trace = "".join(f'<trkpt lat="{50.0 + i * 0.00004:.5f}" lon="{14.6 + (0.00002 if i % 2 else -0.00002):.5f}"/>' for i in range(101))
        fc = {"features": [drawn]}
        summary = gc.check(fc, {}, lambda b, p: GPX.format(pts=trace), fit=True)
        self.assertTrue(drawn["properties"]["gps_fit"])
        self.assertGreaterEqual(drawn["properties"]["gps_cov"], 70)
        self.assertEqual(summary["p"]["fitted"], 1)
        self.assertTrue(all(abs(c[0] - 14.6) < 0.00015 for c in drawn["geometry"]["coordinates"]))  # do ~11 m od stop
        self.assertIn("| 1 |", gc.render(summary))

    def test_join_segments(self):
        # Rodeo ve třech kouscích (prostřední obráceně), Zmije jeden kus, bez názvu se nespojuje
        a = line("p", "Rodeo", n=3)
        b = line("p", "Rodeo", lat0=50.002, n=3)
        b["geometry"]["coordinates"].reverse()
        c = line("p", "Rodeo", lat0=50.001, n=3)
        for f, oid in zip((a, b, c), ("way/1", "way/2", "way/3")):
            f["properties"]["osm"] = oid
        z = line("p", "Zmije", lon=14.7)
        u1, u2 = line("p", None), line("p", None, lat0=50.004)
        out = gc.join_segments([a, b, c, z, u1, u2])
        self.assertEqual(len(out), 4)
        rodeo = [f for f in out if f["properties"]["name"] == "Rodeo"]
        self.assertEqual(len(rodeo), 1)
        self.assertEqual(rodeo[0]["properties"]["segments"], 3)
        self.assertEqual(set(rodeo[0]["properties"]["osm"].split("+")), {"way/1", "way/2", "way/3"})
        lats = [c[1] for c in rodeo[0]["geometry"]["coordinates"]]
        self.assertEqual(lats, sorted(lats))
        self.assertEqual(len(lats), 7)

    def test_fit_skips_short_stubs(self):
        drawn = line("p", "Pahýl", lon=14.6004, n=2)  # 55 m... n=2 → 0.0005° ≈ 56 m; zkrátit
        drawn["geometry"]["coordinates"][1][1] = 50.0003  # ≈ 33 m
        trace = "".join(f'<trkpt lat="{50.0 + i * 0.00004:.5f}" lon="14.60000"/>' for i in range(101))
        gc.check({"features": [drawn]}, {}, lambda b, p: GPX.format(pts=trace), fit=True)
        self.assertNotIn("gps_fit", drawn["properties"])

    def test_fit_leaves_line_without_traces(self):
        drawn = line("p", "Ghost", lon=14.62)
        trace = "".join(f'<trkpt lat="{50.0 + i * 0.0001:.5f}" lon="14.60003"/>' for i in range(41))
        gc.check({"features": [drawn]}, {}, lambda b, p: GPX.format(pts=trace), fit=True)
        self.assertNotIn("gps_fit", drawn["properties"])
        self.assertEqual(drawn["geometry"]["coordinates"][0][0], 14.62)


if __name__ == "__main__":
    unittest.main()
