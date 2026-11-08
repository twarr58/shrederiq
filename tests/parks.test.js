const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const P = require("../web/parks.js");
const parks = JSON.parse(fs.readFileSync(path.join(__dirname, "../web/data/parks.geojson"), "utf8"));

const park = (props, coords = [15.5, 49.8]) => ({
  type: "Feature",
  geometry: { type: "Point", coordinates: coords },
  properties: { id: "test", name: "Test", kind: "bikepark", ...props },
});
const fc = (...features) => ({ type: "FeatureCollection", features });

test("web/data/parks.geojson projde validací", () => {
  assert.deepEqual(P.validateParks(parks), []);
  assert.ok(parks.features.length > 0);
});

test("validace odhalí prohozené souřadnice, duplicitní id a špatný kind", () => {
  const errors = P.validateParks(fc(
    park({ id: "a" }, [49.8, 15.5]),
    park({ id: "b" }),
    park({ id: "b", kind: "skatepark" }),
  ));
  assert.equal(errors.length, 3);
  assert.match(errors[0], /mimo ČR/);
  assert.match(errors[1], /duplicitní id/);
  assert.match(errors[2], /kind/);
});

test("validace kontroluje volitelná metadata", () => {
  const errors = P.validateParks(fc(park({ web: "http://example.com", lift: "ano", trails_count: -1, parking: [50.1, 17.1] })));
  assert.equal(errors.length, 4);
  assert.deepEqual(P.validateParks(fc(park({ web: "https://example.com", lift: null, trails_count: 12, parking: [17.1, 50.1] }))), []);
});

test("closed je neprázdný text nebo null", () => {
  assert.equal(P.validateParks(fc(park({ closed: "" }))).length, 1);
  assert.equal(P.validateParks(fc(park({ closed: true }))).length, 1);
  assert.deepEqual(P.validateParks(fc(park({ closed: "zavřeno 2025" }))), []);
  assert.deepEqual(P.validateParks(fc(park({ closed: null }))), []);
});

test("vyhledávání ignoruje diakritiku a hledá i v obci a kraji, výsledek je seřazený", () => {
  const all = fc(
    park({ id: "spicak", name: "Bikepark Špičák", town: "Železná Ruda", region: "Plzeňský" }),
    park({ id: "kouty", name: "Bikepark Kouty", town: "Kouty nad Desnou", region: "Olomoucký" }),
    park({ id: "cerna", name: "Bikepark Černá hora", town: "Janské Lázně", region: "Královéhradecký" }),
  ).features;
  assert.deepEqual(P.filterParks(all, "spicak").map((f) => f.properties.id), ["spicak"]);
  assert.deepEqual(P.filterParks(all, "zelezna").map((f) => f.properties.id), ["spicak"]);
  assert.deepEqual(P.filterParks(all, "OLOMOUC").map((f) => f.properties.id), ["kouty"]);
  assert.deepEqual(P.filterParks(all, "").map((f) => f.properties.name),
    ["Bikepark Černá hora", "Bikepark Kouty", "Bikepark Špičák"]);
});

test("info panel zobrazí neznámé hodnoty jako neověřeno", () => {
  const rows = Object.fromEntries(P.describePark({ kind: "trail_center", town: "Černá Voda", region: "Olomoucký", lift: null, trails_count: null }));
  assert.equal(rows["Typ"], "Trailpark / singltrek");
  assert.equal(rows["Lokalita"], "Černá Voda, Olomoucký kraj");
  assert.equal(rows["Lanovka / vlek"], "neověřeno");
  assert.equal(rows["Počet trailů"], "neověřeno");
});

test("kraj: Vysočina a Praha mají vlastní tvar", () => {
  const loc = (region) => Object.fromEntries(P.describePark({ kind: "bikepark", town: "X", region }))["Lokalita"];
  assert.equal(loc("Olomoucký"), "X, Olomoucký kraj");
  assert.equal(loc("Vysočina"), "X, Kraj Vysočina");
  assert.equal(loc("Praha"), "X, Praha");
});

test("data z OSM: posun na základnu, parkoviště, ověřené parky se nehýbou", () => {
  const parks = [park({ id: "kouty" }, [17.098, 50.106]), park({ id: "overeny", verified: true }, [15.5, 49.8]),
    park({ id: "rucni", parking: [16.0, 50.0] }, [16.1, 50.1]), park({ id: "nic" })];
  const info = {
    kouty: { base: [17.12, 50.09], base_source: "lift", lift: "LD Medvědí hora", parking: [17.1195, 50.0895], parking_name: "P Kouty" },
    overeny: { base: [15.6, 49.9], base_source: "lift", lift: "X" },
    rucni: { base: [16.2, 50.2], base_source: "parking", parking: [16.2, 50.2] },
  };
  const [kouty, overeny, rucni, nic] = P.applyOsm(parks, info);
  assert.deepEqual(kouty.geometry.coordinates, [17.12, 50.09]);
  assert.deepEqual(kouty.properties.point_orig, [17.098, 50.106]);
  assert.deepEqual([kouty.properties.parking, kouty.properties.parking_name, kouty.properties.osm_lift], [[17.1195, 50.0895], "P Kouty", "LD Medvědí hora"]);
  assert.deepEqual(overeny.geometry.coordinates, [15.5, 49.8]);
  assert.deepEqual(rucni.properties.parking, [16.0, 50.0]);
  assert.equal(nic, parks[3]);
  assert.deepEqual(parks[0].geometry.coordinates, [17.098, 50.106]); // vstup se nemění
  assert.equal(P.applyOsm(parks, null), parks);
});

test("trailforks_rid musí být kladné celé číslo", () => {
  const park = (extra) => ({ type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Point", coordinates: [14.6, 49.5] },
    properties: { id: "p", name: "P", kind: "bikepark", ...extra } }] });
  assert.deepEqual(P.validateParks(park({ trailforks_rid: 1234 })), []);
  assert.equal(P.validateParks(park({ trailforks_rid: "1234" })).length, 1);
  assert.equal(P.validateParks(park({ trailforks_rid: 0 })).length, 1);
});
