import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import osm_extract as ox  # noqa: E402
import park_osm as po  # noqa: E402

try:
    import osmium  # noqa: F401
    HAVE_OSMIUM = True
except ImportError:
    HAVE_OSMIUM = False

PARK = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [17.10, 50.10]},
        "properties": {"id": "kouty", "name": "Bikepark Kouty", "lift": True}}

# uzly 1–4 lanovka (zdola nahoru), 10–13 lajna, 20–22 pěšina v relaci, 30–31 daleko, 40 parkoviště
OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
  <node id="1" lat="50.0900" lon="17.1000"/><node id="2" lat="50.0950" lon="17.1000"/>
  <node id="3" lat="50.1000" lon="17.1000"/><node id="4" lat="50.1050" lon="17.1000"/>
  <node id="10" lat="50.1040" lon="17.1010"/><node id="11" lat="50.1000" lon="17.1012"/>
  <node id="12" lat="50.0960" lon="17.1014"/><node id="13" lat="50.0910" lon="17.1016"/>
  <node id="20" lat="50.1100" lon="17.1100"/><node id="21" lat="50.1110" lon="17.1110"/><node id="22" lat="50.1120" lon="17.1120"/>
  <node id="30" lat="49.5000" lon="15.0000"/><node id="31" lat="49.5010" lon="15.0010"/>
  <node id="40" lat="50.0895" lon="17.0995"><tag k="amenity" v="parking"/><tag k="name" v="P Kouty"/></node>
  <node id="50" lat="49.0" lon="15.0"><tag k="sport" v="pump_track"/><tag k="name" v="Pumptrack Telč"/></node>
  <node id="60" lat="50.2" lon="17.2"/><node id="61" lat="50.2" lon="17.201"/><node id="62" lat="50.201" lon="17.201"/>
  <node id="70" lat="50.0850" lon="17.0950"><tag k="place" v="village"/><tag k="name" v="Kouty nad Desnou"/></node>
  <node id="71" lat="50.0905" lon="17.1005"><tag k="leisure" v="bike_park"/><tag k="name" v="Bikepark Kouty"/></node>
  <way id="100"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><tag k="aerialway" v="chair_lift"/><tag k="name" v="LD Medvědí hora"/></way>
  <way id="101"><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><tag k="highway" v="path"/><tag k="mtb:scale" v="2"/><tag k="name" v="Flow"/></way>
  <way id="102"><nd ref="20"/><nd ref="21"/><nd ref="22"/><tag k="highway" v="path"/></way>
  <way id="103"><nd ref="30"/><nd ref="31"/><tag k="highway" v="path"/><tag k="mtb:scale" v="1"/></way>
  <way id="104"><nd ref="60"/><nd ref="61"/><nd ref="62"/><nd ref="60"/><tag k="leisure" v="track"/><tag k="sport" v="bmx"/><tag k="surface" v="asphalt"/></way>
  <way id="105"><nd ref="60"/><nd ref="61"/><tag k="leisure" v="track"/><tag k="sport" v="running"/></way>
  <relation id="500"><member type="way" ref="102" role=""/><tag k="type" v="route"/><tag k="route" v="mtb"/><tag k="name" v="Singltrek Kouty"/></relation>
</osm>
"""


@unittest.skipUnless(HAVE_OSMIUM, "pyosmium není nainstalované")
class Country(unittest.TestCase):
    def test_extract_country(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cz.osm"
            path.write_text(OSM_XML)
            data = ox.extract_country(str(path))
        by = {(e["type"], e["id"]): e for e in data["elements"]}
        self.assertIn(("way", 101), by)                 # mtb:scale u lanovky (do 1,5 km)
        self.assertIn("geometry", by[("way", 101)])
        self.assertNotIn(("way", 103), by)              # mtb:scale daleko od lanovky
        self.assertEqual(by[("way", 100)]["tags"]["aerialway"], "chair_lift")
        self.assertIn("center", by[("way", 100)])
        self.assertEqual(by[("node", 70)]["tags"]["place"], "village")
        self.assertEqual(by[("node", 71)]["tags"]["leisure"], "bike_park")
        self.assertIn("center", by[("relation", 500)])  # relace route=mtb „Singltrek Kouty“ se středem z členů
        self.assertAlmostEqual(by[("relation", 500)]["center"]["lat"], 50.111, places=3)
        self.assertIn(("way", 104), by)                 # BMX dráha jako kotva (sport=bmx, leisure=track)
        # výstup je použitelný pro celorepublikový report beze změn
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import fetch_osm_parks as fo
        trails, anchors, places = fo.parse_overpass(fo.check_response(data))
        self.assertEqual([t.osm_id for t in trails], [101])
        self.assertEqual(len(places), 1)
        self.assertGreaterEqual(len(anchors), 3)


class Helpers(unittest.TestCase):
    def test_country_trail_ok(self):
        ok = ox.country_trail_ok
        self.assertTrue(ok({"highway": "path", "mtb:scale": "2"}))
        self.assertFalse(ok({"highway": "track", "mtb:scale": "0", "piste:type": "nordic"}))     # běžkařská stopa
        self.assertFalse(ok({"highway": "path", "mtb:scale:imba": "1", "bicycle": "no"}))
        self.assertFalse(ok({"highway": "path", "mtb:scale:imba": "2", "sac_scale": "mountain_hiking"}))
        self.assertTrue(ok({"highway": "path", "mtb:scale": "1", "sac_scale": "hiking"}))

    def test_is_trail(self):
        self.assertTrue(ox.is_trail({"highway": "path", "mtb:scale": "1"}))
        self.assertTrue(ox.is_trail({"highway": "path", "bicycle": "designated"}))
        self.assertTrue(ox.is_trail({"mtb:scale:imba": "2"}))
        self.assertTrue(ox.is_trail({"highway": "path", "mtb:scale:dh": "3"}))
        # Peklák: šlapací trail jako cyklostezka a Růžovka s mtb:scale=0 bez bicycle=designated
        self.assertTrue(ox.is_trail({"highway": "cycleway", "foot": "no", "oneway": "yes", "name": "Dechovka"}))
        self.assertTrue(ox.is_trail({"highway": "path", "foot": "no", "oneway": "yes", "mtb:scale": "0"}))
        self.assertFalse(ox.is_trail({"highway": "cycleway", "oneway": "yes"}))
        self.assertFalse(ox.is_trail({"highway": "cycleway", "foot": "no"}))
        self.assertTrue(ox.is_trail({"highway": "path", "mtb": "single track"}))
        self.assertFalse(ox.is_trail({"highway": "path", "mtb": "no"}))
        self.assertFalse(ox.is_trail({"highway": "residential", "mtb:scale": "0"}))
        self.assertFalse(ox.is_trail({"highway": "path"}))

    def test_is_pumptrack(self):
        self.assertTrue(ox.is_pumptrack({"sport": "pump_track"}))
        self.assertTrue(ox.is_pumptrack({"leisure": "track", "sport": "bmx"}))
        self.assertTrue(ox.is_pumptrack({"leisure": "pitch", "sport": "cycling;bmx"}))
        self.assertTrue(ox.is_pumptrack({"highway": "path", "name": "Pumptrack Lipno"}))
        self.assertFalse(ox.is_pumptrack({"leisure": "track", "sport": "running"}))
        self.assertFalse(ox.is_pumptrack({"leisure": "track"}))

    def test_near(self):
        near = ox.Near([PARK], 3500)
        self.assertTrue(near(50.12, 17.12))
        self.assertFalse(near(50.2, 17.1))


@unittest.skipUnless(HAVE_OSMIUM, "pyosmium není nainstalovaný")
class Extract(unittest.TestCase):
    def test_extract_and_build(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.osm"
            path.write_text(OSM_XML)
            data = ox.extract(str(path), [PARK])
        ids = {(e["type"], e["id"]) for e in data["elements"]}
        self.assertEqual(ids, {("way", 100), ("way", 101), ("way", 102), ("node", 40), ("relation", 500)})
        # výstup je čitelný pro park_osm stejně jako odpověď Overpass
        fc, info, rows = po.build([PARK], po.parse(json.loads(json.dumps(data))))
        self.assertEqual(info["kouty"]["base"], [17.1, 50.09])
        self.assertEqual(info["kouty"]["parking_name"], "P Kouty")
        names = {f["properties"]["name"] for f in fc["features"]}
        self.assertEqual(names, {"Flow", "Singltrek Kouty"})

    def test_extract_pumptracks(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.osm"
            path.write_text(OSM_XML)
            fc = ox.extract_pumptracks(str(path))
        got = {f["properties"]["osm"]: f for f in fc["features"]}
        self.assertEqual(set(got), {"node/50", "way/104"})
        self.assertEqual(got["node/50"]["properties"]["name"], "Pumptrack Telč")
        self.assertEqual(got["way/104"]["properties"]["surface"], "asphalt")
        self.assertAlmostEqual(got["way/104"]["geometry"]["coordinates"][1], 50.2003, places=3)


if __name__ == "__main__":
    unittest.main()
