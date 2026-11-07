import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_osm_parks as osm  # noqa: E402


def feature(id_, name, lon, lat, town=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": id_, "name": name, "town": town}}


def way(id_, lat, lon, tags, n=5, step=0.001):
    """Lajna z n bodů směrem na sever, každý krok ≈ 111 m."""
    return {"type": "way", "id": id_, "tags": tags,
            "geometry": [{"lat": lat + i * step, "lon": lon} for i in range(n)]}


DH = {"highway": "path", "mtb:scale:dh": "2"}
IMBA = {"highway": "path", "mtb:scale:imba": "1"}

OVERPASS = {"elements": [
    # bikepark u Kout: 3 dh lajny vedle sebe (rozestup ~140 m), každá ~440 m → 1,3 km... + 4. lajna
    way(1, 50.100, 17.100, DH), way(2, 50.100, 17.102, DH), way(3, 50.100, 17.104, DH), way(4, 50.100, 17.106, IMBA),
    # singltreky u Trutnova: 4 imba lajny
    way(10, 50.580, 15.920, IMBA), way(11, 50.580, 15.922, IMBA), way(12, 50.580, 15.924, IMBA), way(13, 50.580, 15.926, IMBA),
    # osamocená lajna – žádný shluk
    way(20, 49.000, 15.000, DH),
    # dvojí výskyt stejné cesty v odpovědi
    way(1, 50.100, 17.100, DH),
    # obce
    {"type": "node", "id": 300, "lat": 50.106, "lon": 17.098, "tags": {"place": "village", "name": "Kouty nad Desnou"}},
    {"type": "node", "id": 301, "lat": 50.561, "lon": 15.913, "tags": {"place": "town", "name": "Trutnov"}},
    # kotvy
    {"type": "way", "id": 100, "center": {"lat": 50.101, "lon": 17.103},
     "tags": {"leisure": "bike_park", "name": "Bikepark Kouty", "website": "https://example.cz"}},
    {"type": "relation", "id": 200, "center": {"lat": 50.582, "lon": 15.923},
     "tags": {"type": "route", "route": "mtb", "name": "Trutnov Trails – modrá"}},
]}


class Helpers(unittest.TestCase):
    def test_fold_and_tokens(self):
        self.assertEqual(osm.fold("Bikepark Špičák – Železná Ruda"), "bikepark spicak zelezna ruda")
        self.assertEqual(osm.name_tokens("Bike park Špičák"), {"spicak"})

    def test_name_score(self):
        self.assertEqual(osm.name_score("Bikepark Špičák", "Bike Park Spicak"), 1.0)
        self.assertEqual(osm.name_score("Bikepark Kouty", "Bikepark Ještěd"), 0.0)
        self.assertEqual(osm.name_score(None, "Kouty"), 0.0)

    def test_distances(self):
        self.assertAlmostEqual(osm.haversine_m(50.0, 15.0, 50.01, 15.0), 1112, delta=2)
        self.assertAlmostEqual(osm.line_length_m([(50.0, 15.0), (50.005, 15.0), (50.01, 15.0)]), 1112, delta=2)


class Response(unittest.TestCase):
    def test_check_response(self):
        self.assertIs(osm.check_response(OVERPASS), OVERPASS)
        with self.assertRaises(osm.OverpassError):
            osm.check_response({"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 4 after 241 seconds."})
        with self.assertRaises(osm.OverpassError):
            osm.check_response({"elements": []})


class Clustering(unittest.TestCase):
    def setUp(self):
        self.trails, self.anchors = osm.parse_overpass(OVERPASS)[:2]
        self.clusters = {c.trails[0].osm_id // 10 * 10 if c.trails[0].osm_id >= 10 else 0: c
                         for c in osm.build_clusters(self.trails, self.anchors)}

    def test_parse_dedups_ways(self):
        self.assertEqual(len(self.trails), 9)
        self.assertEqual(len(self.anchors), 2)

    def test_two_clusters_lonely_trail_dropped(self):
        self.assertEqual(set(self.clusters), {0, 10})
        self.assertEqual(len(self.clusters[0].trails), 4)
        self.assertEqual(self.clusters[0].id, "c1")

    def test_cluster_kind_name_website(self):
        kouty, trutnov = self.clusters[0], self.clusters[10]
        self.assertEqual(kouty.kind, "bikepark")        # 3 dh vs 1 imba
        self.assertEqual(trutnov.kind, "trail_center")  # samé imba
        self.assertEqual(kouty.name, "Bikepark Kouty")
        self.assertEqual(kouty.website, "https://example.cz")
        self.assertEqual(trutnov.name, "Trutnov Trails – modrá")
        self.assertAlmostEqual(kouty.length_m, 4 * 445, delta=10)

    def test_lift_trails_make_bikepark_and_unnamed_cluster_gets_place(self):
        lift_path = {"highway": "path", "mtb:scale": "2"}
        data = {"elements": [way(1, 50.3, 16.4, lift_path, n=6), way(2, 50.3, 16.402, lift_path, n=6), way(3, 50.3, 16.404, IMBA, n=6),
                             {"type": "node", "id": 9, "lat": 50.31, "lon": 16.41, "tags": {"place": "town", "name": "Česká Třebová"}}]}
        trails, anchors, places = osm.parse_overpass(data)
        self.assertEqual([t.style for t in trails], ["lift", "lift", "imba"])
        (c,) = osm.build_clusters(trails, anchors, places)
        self.assertEqual(c.kind, "bikepark")
        self.assertIsNone(c.name)
        self.assertEqual(c.label, "u obce Česká Třebová")

    def test_link_distance(self):
        far = {"elements": [way(1, 50.0, 15.0, DH), way(2, 50.0, 15.02, DH)]}  # ~1,4 km od sebe
        trails = osm.parse_overpass(far)[0]
        self.assertEqual(len(osm.cluster_trails(trails)), 2)
        self.assertEqual(len(osm.cluster_trails(trails, link_m=2000)), 1)


class MatchAndReport(unittest.TestCase):
    def setUp(self):
        trails, anchors, places = osm.parse_overpass(OVERPASS)
        self.clusters = osm.build_clusters(trails, anchors, places)
        self.features = [
            feature("kouty", "Bikepark Kouty", 17.098, 50.106, "Kouty nad Desnou"),  # ~400 m od středu shluku
            feature("lipno", "Bikepark Lipno", 14.205, 48.630, "Lipno nad Vltavou"),  # v OSM není
        ]

    def test_match(self):
        matches, extra = osm.match_parks(self.features, self.clusters)
        got = {m.park["properties"]["id"]: (m.cluster.id if m.cluster else None) for m in matches}
        self.assertEqual(got, {"kouty": "c1", "lipno": None})
        self.assertEqual([c.name for c in extra], ["Trutnov Trails – modrá"])

    def test_report(self):
        report = osm.render_report(*osm.match_parks(self.features, self.clusters))
        self.assertIn("se shlukem lajn v OSM: **1**", report)
        self.assertIn("| `kouty` | Bikepark Kouty | Bikepark Kouty |", report)
        self.assertIn("⚠️", report)
        self.assertIn("- `lipno` Bikepark Lipno", report)
        self.assertIn("| Trutnov Trails – modrá |", report)
        self.assertIn("trailpark / singltrek", report)
        self.assertIn("ODbL", report)

    def test_geojson_outputs(self):
        pts = osm.clusters_geojson(self.clusters)
        self.assertEqual(len(pts["features"]), 2)
        self.assertEqual(pts["features"][0]["properties"]["kind"], "bikepark")
        lines = osm.trails_geojson(self.clusters)
        self.assertEqual(len(lines["features"]), 8)
        f = lines["features"][0]
        self.assertEqual(f["geometry"]["type"], "LineString")
        self.assertEqual(f["geometry"]["coordinates"][0], [17.1, 50.1])
        self.assertIn("mtb:scale:dh", f["properties"])


if __name__ == "__main__":
    unittest.main()
