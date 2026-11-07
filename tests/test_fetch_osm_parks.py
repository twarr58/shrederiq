import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_osm_parks as osm  # noqa: E402


def feature(id_, name, lon, lat, town=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": id_, "name": name, "town": town}}


OVERPASS = {"elements": [
    {"type": "way", "id": 1, "center": {"lat": 50.1000, "lon": 17.1000},
     "tags": {"leisure": "bike_park", "name": "Bikepark Kouty", "website": "https://example.cz"}},
    {"type": "node", "id": 2, "lat": 49.1630, "lon": 13.2260, "tags": {"leisure": "bike_park", "name": "Bike park Špičák"}},
    {"type": "node", "id": 3, "lat": 50.5000, "lon": 16.0000, "tags": {"leisure": "bike_park", "name": "Nový park"}},
    # MTB cesty: dvě u Kout (jedna duplicitně z obou dotazů), jedna daleko od všeho
    {"type": "way", "id": 10, "center": {"lat": 50.1010, "lon": 17.1010}, "tags": {"mtb:scale": "2"}},
    {"type": "way", "id": 11, "center": {"lat": 50.0990, "lon": 17.0990}, "tags": {"mtb:scale:dh": "1"}},
    {"type": "way", "id": 11, "center": {"lat": 50.0990, "lon": 17.0990}, "tags": {"mtb:scale:dh": "1"}},
    {"type": "way", "id": 12, "center": {"lat": 49.0000, "lon": 15.0000}, "tags": {"mtb:scale": "1"}},
]}


class Helpers(unittest.TestCase):
    def test_fold_and_tokens(self):
        self.assertEqual(osm.fold("Bikepark Špičák – Železná Ruda"), "bikepark spicak zelezna ruda")
        self.assertEqual(osm.name_tokens("Bike park Špičák"), {"spicak"})

    def test_name_score(self):
        self.assertEqual(osm.name_score("Bikepark Špičák", "Bike Park Spicak"), 1.0)
        self.assertEqual(osm.name_score("Bikepark Kouty", "Bikepark Ještěd"), 0.0)
        self.assertEqual(osm.name_score(None, "Kouty"), 0.0)

    def test_haversine(self):
        # 0,01° zeměpisné šířky ≈ 1112 m
        self.assertAlmostEqual(osm.haversine_m(50.0, 15.0, 50.01, 15.0), 1112, delta=2)


class Parse(unittest.TestCase):
    def test_parks_and_trail_counts(self):
        parks = {p.osm_id: p for p in osm.parse_overpass(OVERPASS)}
        self.assertEqual(set(parks), {1, 2, 3})
        self.assertEqual(parks[1].trails, 2)   # dedup way 11, way 12 je mimo okruh
        self.assertEqual(parks[2].trails, 0)
        self.assertEqual(parks[1].url, "https://www.openstreetmap.org/way/1")


class MatchAndReport(unittest.TestCase):
    def setUp(self):
        self.osm = osm.parse_overpass(OVERPASS)
        self.features = [
            feature("kouty", "Bikepark Kouty", 17.098, 50.106, "Kouty nad Desnou"),   # ~700 m od OSM
            feature("spicak", "Bikepark Špičák", 13.226, 49.163, "Železná Ruda"),      # stejné místo
            feature("lipno", "Bikepark Lipno", 14.205, 48.630, "Lipno nad Vltavou"),  # v OSM není
        ]

    def test_match(self):
        matches, extra = osm.match_parks(self.features, self.osm)
        got = {m.park["properties"]["id"]: (m.osm.osm_id if m.osm else None) for m in matches}
        self.assertEqual(got, {"kouty": 1, "spicak": 2, "lipno": None})
        self.assertEqual([o.osm_id for o in extra], [3])

    def test_nearby_without_name_match_is_ignored_beyond_1500m(self):
        far = [feature("x", "Úplně jiný název", 17.12, 50.10)]  # ~1,4 km → ještě se páruje
        self.assertIsNotNone(osm.match_parks(far, self.osm)[0][0].osm)
        far = [feature("x", "Úplně jiný název", 17.13, 50.10)]  # ~2,1 km → už ne
        self.assertIsNone(osm.match_parks(far, self.osm)[0][0].osm)

    def test_report(self):
        report = osm.render_report(*osm.match_parks(self.features, self.osm))
        self.assertIn("nalezeno v OSM: **2**", report)
        self.assertIn("| `kouty` | Bikepark Kouty | Bikepark Kouty |", report)
        self.assertIn("⚠️", report)              # Kouty jsou posunuté o >300 m
        self.assertIn("- `lipno` Bikepark Lipno", report)
        self.assertIn("| Nový park |", report)
        self.assertIn("ODbL", report)

    def test_geojson(self):
        gj = osm.to_geojson(self.osm)
        self.assertEqual(len(gj["features"]), 3)
        kouty = next(f for f in gj["features"] if f["properties"]["osm"] == "way/1")
        self.assertEqual(kouty["properties"]["website"], "https://example.cz")
        self.assertEqual(kouty["geometry"]["coordinates"], [17.1, 50.1])


if __name__ == "__main__":
    unittest.main()
