import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gps_check as gc  # noqa: E402

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.0" creator="OpenStreetMap.org" xmlns="http://www.topografix.com/GPX/1/0">
  <trk><trkseg>{pts}</trkseg></trk>
</gpx>"""


def gpx(*segs, timed=True):
    """GPX s jedním <trk><trkseg> na stopu; body (lon, lat), s <time> = stopa s pořadím."""
    t = "<time>2024-05-01T10:00:00Z</time>" if timed else ""
    body = "".join("<trk><trkseg>" + "".join(f'<trkpt lat="{y:.6f}" lon="{x:.6f}">{t}</trkpt>' for x, y in seg)
                   + "</trkseg></trk>" for seg in segs)
    return f'<?xml version="1.0"?><gpx version="1.0" xmlns="http://www.topografix.com/GPX/1/0">{body}</gpx>'


M_LON = 1 / (111_320 * 0.6428)   # stupňů délky na metr na 50° s. š.
M_LAT = 1 / 111_320


def bundle(east_m, lat0=50.0, lat1=50.004, step=0.00003, jitter=1.0):
    """Svazek bodů stopy podél poledníku 14.6 posunutý o east_m metrů na východ."""
    n = int((lat1 - lat0) / step)
    return [(14.6 + (east_m + (jitter if i % 2 else -jitter)) * M_LON, lat0 + i * step) for i in range(n + 1)]


def line(pid, name, lat0=50.0, lon=14.6, n=9):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat0 + i * 0.0005] for i in range(n)]},
            "properties": {"park_id": pid, "name": name}}


class GpsCheck(unittest.TestCase):
    def test_parse_trackpoints(self):
        # bez <time> = soukromé body seřazené podle souřadnic → pytel
        segs, bag = gc.parse_trackpoints(GPX.format(pts='<trkpt lat="50.0" lon="14.6"/><trkpt lat="50.001" lon="14.601"><ele>5</ele></trkpt>'))
        self.assertEqual((segs, bag), ([], [(14.6, 50.0), (14.601, 50.001)]))
        # stopy s časem po trkseg, kratší než MIN_SEG_PTS se zahodí
        long_seg = [(14.6, 50.0 + i * 0.0001) for i in range(6)]
        segs, bag = gc.parse_trackpoints(gpx(long_seg, [(14.7, 50.0)] * 3))
        self.assertEqual(segs, [long_seg])
        self.assertEqual(bag, [])

    def test_cache_backward_compatible(self):
        # starý formát cache (plochý seznam bodů) se čte jako jeden anonymní pytel,
        # ale load_traces ho stáhne znovu, ať park dostane i rozlišení stop
        drawn = line("p", "Flow")
        key = ",".join(str(v) for v in gc.bbox_of([drawn]))
        flat = [[14.60003, round(50.0 + i * 0.0001, 5)] for i in range(41)]
        segs, bag = gc.from_cache(flat)
        self.assertEqual((segs, len(bag)), ([], 41))
        old = {key: flat}
        trace = "".join(f'<trkpt lat="{50.0 + i * 0.0001:.5f}" lon="14.60003"/>' for i in range(41))
        calls = []
        summary = gc.check({"features": [drawn]}, old, lambda b, p: (calls.append(p), GPX.format(pts=trace))[1], fit=True)
        self.assertEqual(calls, [0])
        self.assertIsInstance(old[key], dict)
        self.assertEqual(summary["p"]["points"], 41)
        self.assertEqual(drawn["properties"]["gps_cov"], 100)
        # nový formát: segmenty + pytel, po načtení z cache stejné
        cache = {}
        seg = bundle(0, lat1=50.001)
        got = gc.load_traces((1, 2, 3, 4), cache, lambda b, p: gpx(seg) if p == 0 else gpx(seg[:3], timed=False))
        self.assertEqual(set(cache["1,2,3,4"]), {"segs", "bag"})
        self.assertEqual(len(got[0]), 1)
        def boom(bbox, page):
            raise AssertionError("cache se nepoužila")
        again = gc.load_traces((1, 2, 3, 4), cache, boom)
        self.assertEqual(len(again[0][0]), len(seg))

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
        self.assertAlmostEqual(drawn["properties"]["gps_fit_shift_m"], 29, delta=3)
        self.assertEqual(summary["p"]["shift"], drawn["properties"]["gps_fit_shift_m"])
        self.assertEqual(summary["p"]["rejected"], {"traces": 0, "shape": 0, "gain": 0, "length": 0, "shift": 0})
        text = gc.render(summary)
        self.assertIn("medián posunu", text)
        self.assertIn(f"| 1 | {summary['p']['shift']} m |", text)

    def test_ridge_picks_nearer_bundle(self):
        # dva rovnoběžné svazky 30 m od sebe (−10 m a +20 m): těžiště by bylo mezi nimi,
        # hřbet zvolí bližší vrchol
        offs = [-10 + d for d in (-1, 0, 1, 0, -1, 1, 0)] + [20 + d for d in (-1, 0, 1, 0, -1, 1, 0, 0, 1, -1)]
        self.assertAlmostEqual(gc.ridge_offset(offs), -10, delta=1)
        self.assertIsNone(gc.ridge_offset([]))
        self.assertIsNone(gc.ridge_offset([-50 + 4 * i for i in range(26)]))  # rovnoměrný šum bez vrcholu
        # celá lajna: OSM 17 m západně od svazku A, svazek B 30 m východně od A → přesune se
        # na A, ne do těžiště obou svazků
        drawn = line("p", "Rodeo", lon=14.6 - 17 * M_LON)
        fc = {"features": [drawn]}
        gc.check(fc, {}, lambda b, p: gpx(bundle(0), bundle(30)), fit=True)
        self.assertTrue(drawn["properties"]["gps_fit"])
        xs = [(c[0] - 14.6) / M_LON for c in drawn["geometry"]["coordinates"]]
        self.assertTrue(all(abs(x) < 4 for x in xs), xs)

    def test_segment_filter_prefers_trace_along_line(self):
        # stopa A jede podél celé lajny 20 m západně; stopa B je sousední lajna 8 m východně
        # jen na třetině délky. Bez filtru by ta třetina skočila na B, s filtrem jde celá na A.
        drawn = line("p", "Korida", lon=14.6 + 20 * M_LON)
        a = bundle(0)
        b = bundle(28, lat1=50.0013)
        grid = gc.Grid.of(([a, b], []))
        coords = [tuple(c) for c in drawn["geometry"]["coordinates"]]
        self.assertEqual(gc.following_segments(coords, grid), {0})
        new, reason, shift = gc.fit_to_traces(coords, grid, gc.coverage(coords, grid), {0})
        self.assertIsNone(reason)
        self.assertTrue(all(abs((x - 14.6) / M_LON) < 4 for x, _ in new))
        pts = gc.resample(coords, gc.FIT_STEP_M)
        unfiltered = gc.ridge_shifts(pts, grid)
        self.assertTrue(any(s is not None and s < -5 for s in unfiltered[:10]))  # bez filtru: B (na východ)
        # bez stopy podél lajny se bere všechno
        self.assertIsNone(gc.following_segments(coords, gc.Grid.of(([b], a))))

    def test_reject_jumps_and_fill(self):
        shifts = [0, 1, 2, 30, 31, 2, 1, None, 0]
        self.assertEqual(gc.reject_jumps(shifts), [0, 1, 2, None, None, 2, 1, None, 0])
        filled = gc.fill_gaps([None, 4, 4, None, None, 10, None])
        self.assertEqual(filled, [4, 4, 4, 6, 8, 10, 10])          # krátké díry a konce dopočítat
        long_gap = gc.fill_gaps([8] + [None] * 8 + [8], gap=3)
        self.assertEqual(long_gap[:5], [8, 6, 4, 2, 0])             # dlouhá díra: plynulý návrat na OSM
        self.assertEqual(long_gap[-4:], [2, 4, 6, 8])

    def test_quality_gate_reasons(self):
        self.assertIsNone(gc.gate(50, 80, 100, 110, 12))
        self.assertEqual(gc.gate(65, 72, 100, 100, 5), "gain")      # +7 bodů nestačí
        self.assertEqual(gc.gate(40, 60, 100, 100, 5), "gain")      # pod 70 %
        self.assertEqual(gc.gate(40, 90, 100, 140, 5), "length")
        self.assertEqual(gc.gate(40, 90, 100, 75, 5), "length")
        self.assertEqual(gc.gate(40, 90, 100, 100, 45), "shift")
        # v souhrnu parku se odmítnuté lajny počítají podle důvodu
        drawn = line("p", "Ghost", lon=14.62)
        summary = gc.check({"features": [drawn]}, {}, lambda b, p: gpx(bundle(0)), fit=True)
        self.assertEqual(summary["p"]["rejected"]["traces"], 1)
        self.assertIsNone(summary["p"]["shift"])

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
