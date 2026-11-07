import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import park_osm as po  # noqa: E402


def park(id_, lon, lat, name=None, lift=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": id_, "name": name or id_, "lift": lift}}


def way(id_, lat, lon, tags, n=5, dlat=0.001, dlon=0.0):
    return {"type": "way", "id": id_, "tags": tags,
            "geometry": [{"lat": lat + i * dlat, "lon": lon + i * dlon} for i in range(n)]}


# Bikepark: lanovka kreslená nahoru (dolní stanice 50.090/17.120), dvě lajny podél ní jen s mtb:scale,
# singltrek jako relace route=mtb z obyčejných pěšin, lesní cesta, dlouhá cyklotrasa, parkoviště u dolní stanice.
DATA = {"elements": [
    way(1, 50.090, 17.120, {"aerialway": "chair_lift", "name": "LD Medvědí hora"}, n=10),
    way(2, 50.098, 17.1205, {"highway": "path", "mtb:scale": "2", "name": "Flow"}, n=8, dlat=-0.001),
    way(3, 50.098, 17.1215, {"highway": "path", "mtb:scale": "3"}, n=8, dlat=-0.001),
    way(4, 50.095, 17.1300, {"highway": "track", "mtb:scale": "0"}, n=5),                 # lesní cesta u lanovky
    way(5, 50.080, 17.1000, {"highway": "path"}, n=5),                                       # člen singltrek relace
    way(6, 50.080, 17.1020, {"highway": "path"}, n=5),
    way(7, 50.070, 17.0900, {"highway": "track"}, n=5),                                      # člen dlouhé cyklotrasy
    way(8, 50.075, 17.0500, {"highway": "path", "mtb:scale": "1"}, n=5),                     # turistická pěšina daleko od lanovky
    {"type": "relation", "id": 100, "tags": {"route": "mtb", "name": "Singltrek Kouty – modrý okruh"},
     "members": [{"type": "way", "ref": 5}, {"type": "way", "ref": 6}]},
    {"type": "relation", "id": 101, "tags": {"route": "mtb", "network": "rcn", "ref": "6153", "name": "Jesenická magistrála"},
     "members": [{"type": "way", "ref": 7}]},
    {"type": "way", "id": 200, "center": {"lat": 50.0895, "lon": 17.1195}, "tags": {"amenity": "parking", "name": "P Kouty"}},
    {"type": "node", "id": 201, "lat": 50.200, "lon": 17.300, "tags": {"amenity": "parking"}},  # daleko
]}


class ParkOsm(unittest.TestCase):
    def setUp(self):
        self.osm = po.parse(DATA)
        self.parks = [park("kouty", 17.098, 50.106, "Bikepark Kouty")]

    def test_parse(self):
        self.assertEqual(set(self.osm["lifts"]), {1})
        self.assertEqual(set(self.osm["rels"]), {100, 101})
        self.assertEqual(len(self.osm["parkings"]), 2)
        self.assertEqual(set(self.osm["ways"]), {2, 3, 4, 5, 6, 7, 8})

    def test_trail_relations_skip_long_routes(self):
        self.assertEqual(po.trail_relations(self.osm), {5: "Singltrek Kouty – modrý okruh", 6: "Singltrek Kouty – modrý okruh"})

    def test_build(self):
        fc, info, rows = po.build(self.parks, self.osm)
        by_osm = {f["properties"]["osm"]: f["properties"] for f in fc["features"]}
        # lajny u lanovky + relace; ne cyklotrasa ani vzdálená pěšina
        self.assertEqual(set(by_osm), {"way/2", "way/3", "way/4", "way/5", "way/6"})
        self.assertEqual(by_osm["way/5"]["name"], "Singltrek Kouty – modrý okruh")
        self.assertIs(by_osm["way/4"]["access"], True)
        self.assertIs(by_osm["way/2"]["access"], False)
        k = info["kouty"]
        self.assertEqual((k["base_source"], k["lift"], k["base"]), ("lift", "LD Medvědí hora", [17.12, 50.09]))
        self.assertEqual((k["parking"], k["parking_name"]), ([17.1195, 50.0895], "P Kouty"))
        pid, name, src, lift, moved, parking, named, segs, cands = rows[0]
        self.assertEqual((src, parking, named, segs), ("lift", "P Kouty", 2, 5))
        self.assertGreater(moved, 1000)
        self.assertEqual(cands[0][0], "LD Medvědí hora")
        self.assertGreater(cands[0][1], 0)

    def test_trail_center_base_is_parking(self):
        data = {"elements": [e for e in DATA["elements"] if e["id"] not in (1,)]
                + [{"type": "node", "id": 202, "lat": 50.0815, "lon": 17.1035, "tags": {"amenity": "parking"}}]}
        osm = po.parse(data)
        _, info, _ = po.build([park("tc", 17.101, 50.081)], osm)
        # i když lanovka v okolí je, trailpark s lift: false se k ní nepřichytí
        _, info2, _ = po.build([park("tc", 17.101, 50.081, lift=False)], self.osm)
        self.assertNotEqual(info2["tc"]["base_source"], "lift")
        self.assertEqual((info["tc"]["base_source"], info["tc"]["base"]), ("parking", [17.1035, 50.0815]))

    def test_lift_without_trails_still_wins_when_close(self):
        # lanovka bez lajn v okolí, 500 m od bodu parku → základna je ona, ne parkoviště kdesi
        data = {"elements": [
            way(1, 50.100, 17.100, {"aerialway": "chair_lift", "name": "LD Kopřivná"}, n=8),
            way(2, 50.120, 17.130, {"highway": "path", "mtb:scale": "2"}, n=4),   # jediný úsek, 3 km od lanovky
            {"type": "node", "id": 40, "lat": 50.121, "lon": 17.131, "tags": {"amenity": "parking"}},
        ]}
        _, info, rows = po.build([park("kop", 17.103, 50.104, lift=True)], po.parse(data))
        self.assertEqual((info["kop"]["base_source"], info["kop"]["lift"]), ("lift", "LD Kopřivná"))
        self.assertEqual(rows[0][8][0][1], 0)  # kandidát se skóre 0
        # stejná lanovka 4 km od bodu už se nebere → základnou je parkoviště u bodu parku
        _, info2, _ = po.build([park("kop", 17.140, 50.125, lift=True)], po.parse(data))
        self.assertEqual(info2["kop"]["base_source"], "parking")

    def test_way_goes_to_nearest_park(self):
        parks = self.parks + [park("jiny", 17.101, 50.081, lift=False)]  # trailpark bez lanovky
        fc, _, _ = po.build(parks, self.osm)
        owners = {f["properties"]["osm"]: f["properties"]["park_id"] for f in fc["features"]}
        self.assertEqual(owners["way/5"], "jiny")
        self.assertEqual(owners["way/2"], "kouty")

    def test_query_mentions_every_park(self):
        q = po.build_query(self.parks + [park("x", 15.0, 50.0)])
        self.assertIn("around:3000,50.10600,17.09800", q)
        self.assertIn("around:3000,50.00000,15.00000", q)
        self.assertIn('relation(around:3000,50.00000,15.00000)["route"="mtb"]', q)
        self.assertIn('["amenity"="parking"]', q)

    def test_fetch_batched_merges_and_dedups(self):
        parks = [park(f"p{i}", 15.0 + i, 50.0) for i in range(5)]
        queries = []

        def fake(q):
            queries.append(q)
            return {"elements": [{"type": "way", "id": 1}, {"type": "way", "id": 100 + len(queries)}]}

        data = po.fetch_batched(parks, fetch=fake, batch=2)
        self.assertEqual(len(queries), 3)            # 2 + 2 + 1 park
        self.assertIn("around:3000,50.00000,19.00000", queries[2])
        self.assertEqual(sorted(e["id"] for e in data["elements"]), [1, 101, 102, 103])

    def test_summary(self):
        _, _, rows = po.build(self.parks, self.osm)
        text = po.render_summary(rows)
        self.assertIn("| `kouty` | Bikepark Kouty | lanovka | LD Medvědí hora |", text)
        self.assertIn("LD Medvědí hora (", text)


if __name__ == "__main__":
    unittest.main()
