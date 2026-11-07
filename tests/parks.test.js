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
