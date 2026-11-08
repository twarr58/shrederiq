import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import park_osm as po  # noqa: E402


def park(id_, lon, lat, name=None, lift=None, town=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": id_, "name": name or id_, "lift": lift, "town": town}}


def way(id_, lat, lon, tags, n=5, dlat=0.001, dlon=0.0):
    return {"type": "way", "id": id_, "tags": tags,
            "geometry": [{"lat": lat + i * dlat, "lon": lon + i * dlon} for i in range(n)]}


# Bikepark: lanovka kreslená nahoru (dolní stanice 50.090/17.120), dvě lajny podél ní jen s mtb:scale,
# singltrek jako relace route=mtb z obyčejných pěšin, lesní cesta, dlouhá cyklotrasa, parkoviště u dolní stanice.
DATA = {"elements": [
    way(1, 50.090, 17.120, {"aerialway": "chair_lift", "name": "LD Medvědí hora"}, n=10),
    way(2, 50.098, 17.1205, {"highway": "path", "mtb:scale": "2", "name": "Flow"}, n=16, dlat=-0.0005),
    way(3, 50.098, 17.1215, {"highway": "path", "mtb:scale": "3"}, n=16, dlat=-0.0005),
    way(4, 50.095, 17.1230, {"highway": "track", "mtb:scale": "0"}, n=5),                 # lesní cesta u lanovky
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

    def test_dh_only_ways_are_trails(self):
        # sjezdovka otagovaná jen mtb:scale:dh, daleko od lanovky i bez relace
        dh = way(1, 50.05, 17.30, {"highway": "path", "mtb:scale:dh": "3", "name": "Expres DH"}, n=6)
        fc, _, _ = po.build([park("k", 17.30, 50.052, "Express Bikepark Karlov", lift=True)], po.parse({"elements": [dh]}))
        self.assertEqual([f["properties"]["name"] for f in fc["features"]], ["Expres DH"])
        self.assertEqual(fc["features"][0]["properties"]["difficulty"], "black")

    def test_named_continuation_without_mtb_tags(self):
        # Peklák: Korida má kus jen s oneway=yes; navazuje na lajnu Korida → patří k ní.
        # Pěšina se stejným názvem daleko od lajny ani pěšina s jiným názvem se nepřiberou.
        ways = [way(1, 50.0, 17.30, {"highway": "path", "mtb:scale": "3", "bicycle": "designated", "name": "Korida"}, n=6),
                way(2, 50.005, 17.30, {"highway": "path", "oneway": "yes", "name": "Korida"}, n=4),      # navazuje na konec
                way(3, 50.008, 17.30, {"highway": "path", "name": "korida"}, n=3),                     # navazuje na úsek 2
                way(4, 50.0, 17.33, {"highway": "path", "name": "Korida"}, n=4),                       # 2 km stranou
                way(5, 50.005, 17.3001, {"highway": "path", "name": "Lesní pěšina"}, n=4),
                way(6, 50.005, 17.3002, {"highway": "path", "bicycle": "no", "name": "Korida"}, n=4)]
        fc, _, _ = po.build([park("p", 17.30, 50.002, "Bikepark Peklák", lift=False)], po.parse({"elements": ways}))
        self.assertEqual(sorted(f["properties"]["osm"] for f in fc["features"]), ["way/1", "way/2", "way/3"])
        # pokračování převezmou obtížnost lajny (všechny úseky stejná barva jako úsek s mtb:scale)
        diffs = {f["properties"]["osm"]: f["properties"]["difficulty"] for f in fc["features"]}
        self.assertIsNotNone(diffs["way/1"])
        self.assertEqual(set(diffs.values()), {diffs["way/1"]})

    def test_connectors_bridge_gaps_and_lead_uphill_to_trail_start(self):
        # Stoupák: dva kusy s mezerou 44 m po lesní cestě, horní konec 133 m od začátku sjezdu
        # Klouzek po pěšině bez značek. Spojky se kreslí jako neznačené úseky lajny.
        ways = [way(1, 50.000, 17.30, {"highway": "path", "bicycle": "designated", "foot": "no", "oneway": "yes", "name": "Stoupák"}, n=5),
                way(2, 50.0044, 17.30, {"highway": "track"}, n=2, dlat=0.0004),                 # 50.0040 → 50.0044 (mezera)
                way(3, 50.0044, 17.30, {"highway": "path", "bicycle": "designated", "foot": "no", "oneway": "yes", "name": "Stoupák"}, n=5),
                way(4, 50.0084, 17.30, {"highway": "path"}, n=13, dlat=0.0001),                  # 50.0084 → 50.0096
                way(5, 50.0096, 17.30, {"highway": "path", "mtb:scale": "2", "bicycle": "designated", "name": "Klouzek"}, n=5),
                way(6, 50.0084, 17.31, {"highway": "path", "bicycle": "no"}, n=13, dlat=0.0001)]  # zákaz kol se nepoužije
        ways[1]["geometry"] = [{"lat": 50.0040, "lon": 17.30}, {"lat": 50.0044, "lon": 17.30}]
        fc, _, _ = po.build([park("s", 17.30, 50.004, "Šemberské stezky", lift=False)], po.parse({"elements": ways}))
        con = [f["properties"] for f in fc["features"] if f["properties"].get("connector")]
        self.assertEqual(sorted((c["name"], round(c["length_m"], -1)) for c in con), [("Stoupák", 40), ("Stoupák", 130)])
        self.assertTrue(all(c["access"] is False for c in con))

    def test_unnamed_ways_far_from_named_trails_are_dropped(self):
        ways = [way(1, 50.000, 17.30, {"highway": "path", "mtb:scale": "2", "bicycle": "designated", "name": "Niki"}, n=6),
                way(2, 50.003, 17.301, {"highway": "path", "mtb:scale": "1", "bicycle": "designated"}, n=4),   # u Niki
                way(3, 50.000, 17.33, {"highway": "path", "mtb:scale": "1", "bicycle": "designated"}, n=4)]   # 2 km stranou
        fc, _, _ = po.build([park("p", 17.30, 50.002, "Trailpark", lift=False)], po.parse({"elements": ways}))
        self.assertEqual(sorted(f["properties"]["osm"] for f in fc["features"]), ["way/1", "way/2"])

    def test_access_tracks_far_from_trails_are_dropped(self):
        def feat(osm, lon, access):
            return {"geometry": {"coordinates": [[lon, 50.0], [lon, 50.002]]},
                    "properties": {"park_id": "p", "osm": osm, "access": access}}
        fs = [feat("trail", 17.30, False), feat("near", 17.302, True), feat("far", 17.33, True),
              {**feat("other", 17.40, True), "properties": {"park_id": "q", "osm": "other", "access": True}}]
        po.drop_far_access(fs, "p")
        self.assertEqual([f["properties"]["osm"] for f in fs], ["trail", "near", "other"])

    def test_whole_trail_relation_when_it_touches_the_park(self):
        # okruh „Hejnický hřeben“: první úsek u parku, druhý 6 km daleko – patří k parku celý
        ways = [way(1, 50.000, 17.30, {"highway": "path"}, n=4),
                way(2, 50.050, 17.30, {"highway": "path"}, n=4),
                way(3, 50.060, 17.30, {"highway": "path", "mtb:scale": "1", "bicycle": "designated"}, n=4)]  # mimo relaci, daleko
        rel = {"type": "relation", "id": 9, "tags": {"route": "mtb", "name": "Hejnický hřeben"},
               "members": [{"type": "way", "ref": 1}, {"type": "way", "ref": 2}]}
        fc, _, _ = po.build([park("p", 17.30, 50.002, "Singltrek", lift=False)], po.parse({"elements": ways + [rel]}))
        self.assertEqual(sorted(f["properties"]["osm"] for f in fc["features"] if not f["properties"].get("connector")), ["way/1", "way/2"])

    def test_bikepark_lines_without_mtb_scale(self):
        # Bikepark Kopřivná (Gravity Line, Fun Trail, Spicy Freeride): path + bicycle=designated
        # + mtb=single track, bez mtb:scale, daleko od lanovky
        ways = [way(1, 50.0, 17.30, {"highway": "path", "bicycle": "designated", "mtb": "single track", "name": "Gravity Line"}, n=6),
                way(2, 50.0, 17.302, {"highway": "path", "bicycle": "designated", "name": "Fun Trail"}, n=6),
                way(3, 50.0, 17.304, {"highway": "path", "bicycle": "designated", "name": "Lesní pěšina"}, n=6),  # obyčejná cyklopěšina
                way(4, 50.0, 17.306, {"highway": "path", "mtb": "no", "mtb:scale": "1"}, n=6)]
        fc, _, _ = po.build([park("k", 17.302, 50.002, "Bikepark Kopřivná", lift=True)], po.parse({"elements": ways}))
        self.assertEqual(sorted(f["properties"]["name"] for f in fc["features"]), ["Fun Trail", "Gravity Line"])

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

    def test_lift_choice_rules(self):
        # Černá hora: sedačka Protěž (0 lajn, 280 m), gondola Černohorský Express (0, 680 m),
        # Hofmanky Express (3 body lajn = šum, 1,5 km) → gondola
        gondola = way(1, 50.6305, 15.7900, {"aerialway": "gondola", "name": "Černohorský Express"}, n=10)
        protez = way(2, 50.6330, 15.7820, {"aerialway": "chair_lift", "name": "Protěž"}, n=6)
        hofmanky = way(3, 50.6450, 15.7700, {"aerialway": "chair_lift", "name": "Hofmanky Express"}, n=8)
        trail = way(4, 50.6460, 15.7705, {"highway": "path", "mtb:scale": "1"}, n=3)  # 3 body u Hofmanek
        osm = po.parse({"elements": [gondola, protez, hofmanky, trail]})
        _, info, _ = po.build([park("ch", 15.7840, 50.6310, "Bikepark Černá hora", lift=True)], osm)
        self.assertEqual(info["ch"]["lift"], "Černohorský Express")
        # Kopřivná: lanovka se jménem parku 1,8 km od bodu vyhraje i bez lajn
        kop = way(5, 50.0300, 17.3000, {"aerialway": "chair_lift", "name": "Kopřivná"}, n=8)
        other = way(6, 50.0400, 17.2850, {"aerialway": "chair_lift", "name": "Olomoučák"}, n=8)
        osm2 = po.parse({"elements": [kop, other]})
        _, info2, _ = po.build([park("kop", 17.288, 50.042, "Bikepark Kopřivná", lift=True)], osm2)
        self.assertEqual(info2["kop"]["lift"], "Kopřivná")

    def test_name_rule_prefers_trails_and_ignores_town(self):
        self.assertEqual(po.park_keywords("Bikepark Špindlerův Mlýn", "Špindlerův Mlýn"), "")
        self.assertEqual(po.park_keywords("Bikepark Kopřivná", "Malá Morávka"), "koprivna")
        # Špičák: dvě lanovky se jménem parku → ta s víc lajnami, i když je dál
        far = way(1, 49.170, 13.230, {"aerialway": "chair_lift", "name": "Lanová dráha Špičák"}, n=8)
        near = way(2, 49.164, 13.226, {"aerialway": "chair_lift", "name": "Špičák - Hofmanky"}, n=8)
        trails = [way(10 + i, 49.171, 13.2305 + i * 0.0005, {"highway": "path", "mtb:scale": "2"}, n=16, dlat=0.0004) for i in range(3)]
        osm = po.parse({"elements": [far, near] + trails})
        _, info, _ = po.build([park("sp", 13.226, 49.163, "Bikepark Špičák", lift=True, town="Železná Ruda")], osm)
        self.assertEqual(info["sp"]["lift"], "Lanová dráha Špičák")
        # Špindl: lanovka pojmenovaná po městě nevyhrává jen kvůli jménu → rozhodnou lajny
        medv = way(3, 50.730, 15.600, {"aerialway": "cable_car", "name": "Špindlerův Mlýn - Medvědín"}, n=8)
        hrom = way(4, 50.717, 15.615, {"aerialway": "chair_lift", "name": "Hromovka"}, n=8)
        tr = [way(20 + i, 50.7175, 15.6155 + i * 0.0005, {"highway": "path", "mtb:scale": "2"}, n=16, dlat=0.0004) for i in range(3)]
        osm2 = po.parse({"elements": [medv, hrom] + tr})
        _, info2, _ = po.build([park("sm", 15.605, 50.714, "Bikepark Špindlerův Mlýn", lift=True, town="Špindlerův Mlýn")], osm2)
        self.assertEqual(info2["sm"]["lift"], "Hromovka")

    def test_other_aerialways_listed_when_no_bike_lift(self):
        tbar = way(1, 49.536, 14.598, {"aerialway": "t-bar", "name": "Vlek U lesa"}, n=6)
        _, info, rows = po.build([park("mon", 14.6, 49.535, "Bikepark Monínec", lift=True)], po.parse({"elements": [tbar]}))
        self.assertIsNone(info["mon"]["base_source"])
        self.assertEqual(rows[0][8], [("Vlek U lesa [t-bar]", -1, rows[0][8][0][2])])
        self.assertIn("Vlek U lesa [t-bar] (", po.render_summary(rows))

    def test_drag_lift_named_after_park_is_base(self):
        # Kyčerka: kola vozí kotva „Kyčerka“, sedačka tam není; vlek s cizím jménem základnou není
        tbar = way(1, 49.352, 18.303, {"aerialway": "t-bar", "name": "Kyčerka"}, n=6)
        other = way(2, 49.351, 18.301, {"aerialway": "platter", "name": "Funpark"}, n=6)
        _, info, _ = po.build([park("ky", 18.30, 49.35, "Bikepark Kyčerka", lift=True, town="Velké Karlovice")],
                              po.parse({"elements": [tbar, other]}))
        self.assertEqual((info["ky"]["base_source"], info["ky"]["lift"]), ("lift", "Kyčerka"))
        self.assertEqual(info["ky"]["base"], [round(tbar["geometry"][0]["lon"], 5), round(tbar["geometry"][0]["lat"], 5)])

    def test_parking_fallback_from_park_point(self):
        # lajny daleko od parkovišť, ale u bodu parku parkoviště je
        els = [way(i, 50.20, 15.90 + i * 0.001, {"highway": "path", "mtb:scale:imba": "2"}, n=4) for i in range(1, 5)]
        els.append({"type": "node", "id": 40, "lat": 50.185, "lon": 15.905, "tags": {"amenity": "parking", "name": "P centrum"}})
        _, info, _ = po.build([park("tt", 15.906, 50.186, "Trutnov Trails", lift=False)], po.parse({"elements": els}))
        self.assertEqual((info["tt"]["base_source"], info["tt"].get("parking_name")), ("parking", "P centrum"))

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

    def test_route_relation_beyond_extract_is_skipped(self):
        # dálková MTB trasa: v okolí parku jen dva členy z deseti, bez trailového jména → pryč;
        # stejně dlouhý úsek relace „… Trail“ se bere
        tour = [way(50, 49.50, 14.60, {"highway": "track", "name": "Jezerní cesta"}, n=6, dlon=0.001),
                way(51, 49.50, 14.61, {"highway": "track"}, n=6, dlon=0.001)]
        rel = lambda rid, name, refs: {"type": "relation", "id": rid, "tags": {"route": "mtb", "name": name},
                                       "members": [{"type": "way", "ref": r} for r in refs]}
        osm = po.parse({"elements": tour + [rel(1, "MTB-Tour 2 Rundtour", [50, 51] + list(range(900, 908)))]})
        fc, _, _ = po.build([park("p", 14.605, 49.50, "Bikepark P", lift=True)], osm)
        self.assertEqual(fc["features"], [])
        osm = po.parse({"elements": tour + [rel(1, "Jezerní Trail", [50, 51] + list(range(900, 908)))]})
        fc, _, _ = po.build([park("p", 14.605, 49.50, "Bikepark P", lift=True)], osm)
        self.assertEqual(sorted(f["properties"]["name"] for f in fc["features"]), ["Jezerní Trail", "Jezerní Trail"])

    def test_relation_colour_overrides_mtb_scale(self):
        # Peklák: Korida má mtb:scale 3 (černá), relace ji značí červeně; šlapací Dechovka je cyklostezka
        korida = way(60, 49.50, 14.60, {"highway": "path", "mtb:scale": "3", "name": "Korida", "bicycle": "designated"})
        dechovka = way(61, 49.50, 14.605, {"highway": "cycleway", "foot": "no", "oneway": "yes", "name": "Dechovka"})
        rel = {"type": "relation", "id": 1, "tags": {"route": "mtb", "name": "Korida", "colour": "red"},
               "members": [{"type": "way", "ref": 60}]}
        osm = po.parse({"elements": [korida, dechovka, rel]})
        fc, _, _ = po.build([park("p", 14.602, 49.50, "Bikepark P", lift=True)], osm)
        by_name = {f["properties"]["name"]: f["properties"] for f in fc["features"]}
        self.assertEqual(by_name["Korida"]["difficulty"], "red")
        self.assertIn("Dechovka", by_name)
        self.assertIsNone(by_name["Dechovka"]["difficulty"])
        self.assertEqual(po.relation_difficulty({"colour": "purple", "mtb:scale": "1"}), "blue")

    def test_named_track_with_mtb_scale_is_access_road(self):
        lift = way(1, 49.50, 14.60, {"aerialway": "chair_lift", "name": "Sedačka"}, n=6)
        track = way(2, 49.501, 14.601, {"highway": "track", "mtb:scale": "1", "name": "Jezerní cesta"}, n=6)
        dh = way(3, 49.502, 14.602, {"highway": "track", "mtb:scale:dh": "2", "name": "Rodeo"}, n=6)
        fc, _, rows = po.build([park("p", 14.60, 49.50, "Bikepark P", lift=True)], po.parse({"elements": [lift, track, dh]}))
        by = {f["properties"]["osm"]: f["properties"] for f in fc["features"]}
        self.assertEqual((by["way/2"]["access"], by["way/2"]["name"]), (True, None))
        self.assertEqual((by["way/3"]["access"], by["way/3"]["name"]), (False, "Rodeo"))
        self.assertEqual(rows[0][6], 1)  # jen Rodeo je pojmenovaná lajna

    def test_ski_touring_and_no_bicycle_paths_near_lift_are_not_lines(self):
        lift = way(1, 49.50, 14.60, {"aerialway": "chair_lift", "name": "Sedačka"}, n=6)
        skitour = way(2, 49.501, 14.601, {"highway": "path", "mtb:scale": "0", "piste:type": "skitour"}, n=6)
        forbidden = way(3, 49.502, 14.602, {"highway": "path", "mtb:scale": "1", "bicycle": "no"}, n=6)
        ok = way(4, 49.503, 14.603, {"highway": "path", "mtb:scale": "2", "piste:type": "skitour"}, n=6)
        fc, _, _ = po.build([park("p", 14.60, 49.50, "Bikepark P", lift=True)], po.parse({"elements": [lift, skitour, forbidden, ok]}))
        self.assertEqual([f["properties"]["osm"] for f in fc["features"]], ["way/4"])
