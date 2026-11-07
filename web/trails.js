// Trailové lajny parků (web/data/trails.geojson z OSM) – čisté funkce bez DOM,
// testuje je tests/trails.test.js.
(function (root) {
  "use strict";

  const DIFFICULTY = {
    green: { label: "lehká", color: "#2a9d4b" },
    blue: { label: "střední", color: "#1f6fd1" },
    red: { label: "těžká", color: "#d62828" },
    black: { label: "velmi těžká", color: "#1d2125" },
  };
  const UNKNOWN = { label: "neuvedeno", color: "#8a8f98" };
  const ORDER = ["green", "blue", "red", "black", null];

  function difficultyInfo(d) {
    return DIFFICULTY[d] || UNKNOWN;
  }

  // Lajny jednoho parku + souhrn pro panel. Lajny bez názvu se v seznamu sloučí
  // podle obtížnosti („3× bez názvu“), aby seznam nebyl dlouhý.
  function summarize(fc, parkId) {
    const features = ((fc && fc.features) || []).filter((f) => f.properties && f.properties.park_id === parkId);
    const lengthM = features.reduce((a, f) => a + (f.properties.length_m || 0), 0);

    const named = new Map(); // "name|difficulty" → { name, difficulty, lengthM, segments }
    for (const f of features) {
      const p = f.properties;
      const key = `${p.name || ""}|${p.difficulty || ""}`;
      const row = named.get(key) || { name: p.name || null, difficulty: p.difficulty || null, lengthM: 0, segments: 0 };
      row.lengthM += p.length_m || 0;
      row.segments += 1;
      named.set(key, row);
    }
    const rows = [...named.values()].sort((a, b) =>
      ORDER.indexOf(a.difficulty) - ORDER.indexOf(b.difficulty)
      || (a.name === null) - (b.name === null)
      || (a.name || "").localeCompare(b.name || "", "cs"));

    const byDifficulty = {};
    for (const f of features) {
      const d = f.properties.difficulty || "unknown";
      byDifficulty[d] = (byDifficulty[d] || 0) + (f.properties.length_m || 0);
    }
    return { features, count: features.length, lengthM, rows, byDifficulty };
  }

  const api = { DIFFICULTY, difficultyInfo, summarize };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShrederiqTrails = api;
})(typeof window !== "undefined" ? window : globalThis);
