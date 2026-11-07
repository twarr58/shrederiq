const { test } = require("node:test");
const assert = require("node:assert/strict");
const T = require("../web/trails.js");

const line = (park_id, name, difficulty, length_m) => ({
  type: "Feature",
  geometry: { type: "LineString", coordinates: [[17.1, 50.1], [17.11, 50.11]] },
  properties: { park_id, name, difficulty, length_m },
});

const FC = {
  type: "FeatureCollection",
  features: [
    line("kouty", "Flow", "blue", 800),
    line("kouty", "Flow", "blue", 400),      // stejná lajna rozdělená na dvě cesty v OSM
    line("kouty", "Black Hole", "black", 600),
    line("kouty", null, "red", 300),
    line("kouty", null, "red", 200),
    line("kouty", "Easy", "green", 1000),
    line("kouty", "Bez obtížnosti", null, 100),
    line("spicak", "Jiný park", "red", 999),
  ],
};

test("souhrn lajn jednoho parku", () => {
  const s = T.summarize(FC, "kouty");
  assert.equal(s.count, 7);
  assert.equal(s.lengthM, 3400);
  assert.deepEqual(s.byDifficulty, { blue: 1200, black: 600, red: 500, green: 1000, unknown: 100 });
});

test("seznam: seřazený podle obtížnosti, segmenty stejné lajny sloučené", () => {
  const rows = T.summarize(FC, "kouty").rows;
  assert.deepEqual(rows.map((r) => [r.name, r.difficulty, r.lengthM, r.segments]), [
    ["Easy", "green", 1000, 1],
    ["Flow", "blue", 1200, 2],
    [null, "red", 500, 2],
    ["Black Hole", "black", 600, 1],
    ["Bez obtížnosti", null, 100, 1],
  ]);
});

test("park bez lajn / chybějící data", () => {
  assert.equal(T.summarize(FC, "lipno").count, 0);
  assert.equal(T.summarize(null, "kouty").count, 0);
});

test("barvy a popisky obtížnosti", () => {
  assert.equal(T.difficultyInfo("red").label, "těžká");
  assert.equal(T.difficultyInfo(null).label, "neuvedeno");
  assert.match(T.difficultyInfo("blue").color, /^#[0-9a-f]{6}$/);
});

test("barva podle sklonu", () => {
  assert.equal(T.slopeBin(0), 0);
  assert.equal(T.slopeBin(-4), 0);
  assert.equal(T.slopeBin(5), 1);
  assert.equal(T.slopeBin(-17), 3);
  assert.equal(T.slopeBin(45), 5);
});

test("úseky se stejnou barvou sklonu se spojí do jedné čáry", () => {
  const f = {
    geometry: { coordinates: [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]] },
    properties: { slopes: [-3, -4, -12, 2] },
  };
  const runs = T.slopeRuns(f);
  assert.deepEqual(runs.map((r) => [r.bin, r.coordinates.length, r.slopeMax]), [[0, 3, 4], [2, 2, 12], [0, 2, 2]]);
  assert.equal(T.slopeRuns({ geometry: f.geometry, properties: {} }), null);
  assert.equal(T.slopeRuns({ geometry: f.geometry, properties: { slopes: [1] } }), null);
});

test("souhrn lajn se sklonem", () => {
  const withSlope = (p) => ({ ...line("kouty", "Flow", "blue", 500), properties: { ...line("kouty", "Flow", "blue", 500).properties, ...p } });
  const fc = { features: [withSlope({ slopes: [1], slope_max: 14, ele_min: 800, ele_max: 900 }), withSlope({ slopes: [1], slope_max: 22, ele_min: 700, ele_max: 820 })] };
  const s = T.summarize(fc, "kouty");
  assert.equal(s.hasSlopes, true);
  assert.deepEqual([s.rows[0].slopeMax, s.rows[0].eleMin, s.rows[0].eleMax], [22, 700, 900]);
  assert.equal(T.summarize(FC, "kouty").hasSlopes, false);
});
