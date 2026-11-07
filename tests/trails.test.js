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
