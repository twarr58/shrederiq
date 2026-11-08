import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import elevation as el  # noqa: E402

# 0,001° zeměpisné šířky ≈ 111 m
NORTH = [(15.0, 50.0), (15.0, 50.001), (15.0, 50.002)]  # ~222 m na sever


class Resample(unittest.TestCase):
    def test_spacing_and_endpoints(self):
        pts = el.resample(NORTH, 25)
        self.assertEqual(pts[0], (15.0, 50.0))
        self.assertEqual(pts[-1], (15.0, 50.002))
        gaps = [el.haversine_m(a, b) for a, b in zip(pts, pts[1:])]
        self.assertTrue(all(20 <= g <= 31 for g in gaps), gaps)
        self.assertEqual(len(pts), 10)  # 0, 25, …, 200, 222

    def test_short_line(self):
        self.assertEqual(el.resample([(15.0, 50.0), (15.0, 50.0001)], 25), [(15.0, 50.0), (15.0, 50.0001)])


class Slopes(unittest.TestCase):
    def test_constant_grade(self):
        pts = el.resample(NORTH, 25)
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + el.haversine_m(a, b))
        eles = [500 - d * 0.12 for d in cum]  # 12 % z kopce
        slopes = el.segment_slopes(pts, eles)
        self.assertEqual(len(slopes), len(pts) - 1)
        self.assertTrue(all(s == -12 for s in slopes), slopes)
        s = el.summarize(pts, eles, slopes)
        self.assertEqual((s["slope_avg"], s["slope_max"], s["ele_max"]), (12.0, 12, 500))

    def test_window_smooths_noise(self):
        pts = el.resample(NORTH, 25)
        eles = [100, 100, 106, 100, 100, 100, 100, 100, 100, 100]  # jeden špatný bod DEM
        self.assertLessEqual(max(abs(s) for s in el.segment_slopes(pts, eles)), 8)  # bez okna by to bylo ±24 %


class Enrich(unittest.TestCase):
    def test_enrich_uses_cache_and_batches(self):
        calls = []

        def fake_fetch(points):
            calls.append(len(points))
            return [1000 - (p[1] - 50.0) * 111_000 * 0.1 for p in points]  # 10 % z kopce na sever

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(p) for p in NORTH]},
             "properties": {"park_id": "kouty", "name": "Flow"}},
        ]}
        cache = {}
        n = el.enrich(fc, cache, fake_fetch)
        p = fc["features"][0]["properties"]
        self.assertEqual(n, 1)
        self.assertEqual(len(p["slopes"]), len(fc["features"][0]["geometry"]["coordinates"]) - 1)
        self.assertEqual(p["slope_max"], 10)
        self.assertAlmostEqual(p["slope_avg"], 10.0, delta=0.3)
        self.assertEqual((p["ele_max"], p["ele_min"]), (1000, 978))
        # podruhé už všechno z cache, žádný dotaz
        fc2 = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(p) for p in NORTH]}, "properties": {}}]}
        self.assertEqual(el.enrich(fc2, cache, fake_fetch), 0)
        self.assertEqual(calls, [10])

    def test_enrich_keeps_geometry(self):
        # serpentina s body po 5 m: geometrie zůstane, sklon i výšky jsou na původních bodech
        zigzag = [(15.0 + (0.00007 if i % 2 else 0.0), 50.0 + i * 0.000045) for i in range(40)]
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(p) for p in zigzag]}, "properties": {}}]}
        el.enrich(fc, {}, lambda pts: [500.0 + (p[1] - 50.0) * 111_000 * 0.1 for p in pts])
        f = fc["features"][0]
        self.assertEqual(f["geometry"]["coordinates"], [list(p) for p in zigzag])
        self.assertEqual(len(f["properties"]["slopes"]), len(zigzag) - 1)
        self.assertEqual(f["properties"]["ele_min"], 500)

    def test_interpolate(self):
        self.assertEqual(el.interpolate([0, 5, 10, 12], [0, 10], [100, 200]), [100, 150, 200, 200])

    def test_batches_of_256(self):
        cache, sizes = {}, []
        pts = [(15.0 + i * 0.0001, 50.0) for i in range(600)]
        el.lookup_elevations(pts, cache, lambda p: (sizes.append(len(p)), [0.0] * len(p))[1])
        self.assertEqual(sizes, [256, 256, 88])


if __name__ == "__main__":
    unittest.main()
