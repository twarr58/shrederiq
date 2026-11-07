import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import snap_trails as st  # noqa: E402

# rovná lajna 50.0→50.004 (asi 445 m) na lon 14.6
OSM = [[14.6, 50.0 + i * 0.0005] for i in range(9)]


def feature(coords=OSM, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]},
            "properties": {"park_id": "p", "name": "Flow", "osm": "way/1", "length_m": 445, **props}}


def fake_route(shift_lon=0.0, zigzag=False):
    """Routing kopírující OSM posunutý o shift_lon (stupně); zigzag = klikatá trasa
    kmitající ±14 m kolem lajny (blízko, ale několikrát delší)."""
    def route(start, end, via):
        n = 81 if zigzag else 9
        coords = []
        for i in range(n):
            dx = shift_lon + (0.0002 if zigzag and i % 2 else -0.0002 if zigzag else 0)
            coords.append([start[0] + dx, start[1] + i * (end[1] - start[1]) / (n - 1)])
        route.calls.append((start, end, via))
        return {"length": 445, "geometry": {"type": "LineString", "coordinates": coords}}
    route.calls = []
    return route


class Snap(unittest.TestCase):
    def test_snaps_when_close(self):
        f, cache, route = feature(), {}, fake_route(shift_lon=0.0001)  # ~7 m vedle
        self.assertEqual(st.snap_feature(f, cache, route), "snapped")
        self.assertTrue(f["properties"]["snapped"])
        self.assertAlmostEqual(f["geometry"]["coordinates"][0][0], 14.6001)
        self.assertEqual(len(route.calls), 1)
        self.assertLessEqual(len(route.calls[0][2]), st.MAX_WAYPOINTS)
        self.assertGreaterEqual(len(route.calls[0][2]), 3)   # po 100 m na 445 m
        # podruhé z cache
        f2 = feature()
        self.assertEqual(st.snap_feature(f2, cache, route), "snapped")
        self.assertEqual(len(route.calls), 1)

    def test_keeps_osm_when_far_or_much_longer(self):
        f = feature()
        self.assertEqual(st.snap_feature(f, {}, fake_route(shift_lon=0.001)), "kept")  # ~70 m vedle
        self.assertEqual(f["geometry"]["coordinates"], OSM)
        self.assertGreater(f["properties"]["snap_dev"], 25)
        f = feature()
        self.assertEqual(st.snap_feature(f, {}, fake_route(zigzag=True)), "kept")      # blízko, ale mnohem delší

    def test_skips_access_gpx_and_short(self):
        route = fake_route()
        self.assertEqual(st.snap_feature(feature(access=True), {}, route), "skipped")
        self.assertEqual(st.snap_feature(feature(source="gpx"), {}, route), "skipped")
        self.assertEqual(st.snap_feature(feature(coords=OSM[:2]), {}, route), "skipped")
        self.assertEqual(route.calls, [])

    def test_routing_error_keeps_osm_and_is_cached(self):
        def boom(start, end, via):
            raise RuntimeError("HTTP 403")
        f, cache = feature(), {}
        self.assertEqual(st.snap_feature(f, cache, boom), "kept")
        self.assertIn("error", next(iter(cache.values())))

    def test_waypoints_thinned(self):
        long = [(14.6, 50.0 + i * 0.0005) for i in range(81)]  # ~4,4 km → 44 bodů po 100 m
        self.assertEqual(len(st.waypoints(long)), st.MAX_WAYPOINTS)

    def test_report(self):
        fc = {"features": [feature(snap_dev=40), feature(snapped=True)]}
        self.assertIn("| `p` | Flow | 40 m |", st.report(fc))
        self.assertIn("sedí", st.report({"features": [feature(snapped=True)]}))


if __name__ == "__main__":
    unittest.main()
