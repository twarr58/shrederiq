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
  <way id="100"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><tag k="aerialway" v="chair_lift"/><tag k="name" v="LD Medvědí hora"/></way>
  <way id="101"><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><tag k="highway" v="path"/><tag k="mtb:scale" v="2"/><tag k="name" v="Flow"/></way>
  <way id="102"><nd ref="20"/><nd ref="21"/><nd ref="22"/><tag k="highway" v="path"/></way>
  <way id="103"><nd ref="30"/><nd ref="31"/><tag k="highway" v="path"/><tag k="mtb:scale" v="1"/></way>
  <relation id="500"><member type="way" ref="102" role=""/><tag k="type" v="route"/><tag k="route" v="mtb"/><tag k="name" v="Singltrek Kouty"/></relation>
</osm>
"""


class Helpers(unittest.TestCase):
    def test_is_trail(self):
        self.assertTrue(ox.is_trail({"highway": "path", "mtb:scale": "1"}))
        self.assertTrue(ox.is_trail({"highway": "path", "bicycle": "designated"}))
        self.assertTrue(ox.is_trail({"mtb:scale:imba": "2"}))
        self.assertFalse(ox.is_trail({"highway": "residential", "mtb:scale": "0"}))
        self.assertFalse(ox.is_trail({"highway": "path"}))

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


if __name__ == "__main__":
    unittest.main()
