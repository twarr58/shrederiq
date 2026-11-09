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

  // Sklon (|%|) → barva; hranice horní (do 5 %, 5–10 %, …).
  const SLOPE_BINS = [
    { max: 5, color: "#2a9d4b", label: "do 5 %" },
    { max: 10, color: "#a5c63b", label: "5–10 %" },
    { max: 15, color: "#f2c12e", label: "10–15 %" },
    { max: 20, color: "#f08a24", label: "15–20 %" },
    { max: 30, color: "#d62828", label: "20–30 %" },
    { max: Infinity, color: "#6a1b9a", label: "nad 30 %" },
  ];

  function slopeBin(pct) {
    const a = Math.abs(pct);
    return SLOPE_BINS.findIndex((b) => a < b.max);
  }

  // Úseky lajny se stejnou barvou sklonu spojí do jedné čáry:
  // [{ coordinates: [[lon, lat], …], bin, color, slopeMax }]
  function slopeRuns(feature) {
    const coords = feature.geometry.coordinates;
    const slopes = feature.properties.slopes;
    if (!Array.isArray(slopes) || slopes.length !== coords.length - 1) return null;
    const runs = [];
    slopes.forEach((s, i) => {
      const bin = slopeBin(s);
      const last = runs[runs.length - 1];
      if (last && last.bin === bin) {
        last.coordinates.push(coords[i + 1]);
        last.slopeMax = Math.max(last.slopeMax, Math.abs(s));
      } else {
        runs.push({ coordinates: [coords[i], coords[i + 1]], bin, color: SLOPE_BINS[bin].color, slopeMax: Math.abs(s) });
      }
    });
    return runs;
  }

  function hasSlopes(features) {
    return features.some((f) => Array.isArray(f.properties.slopes));
  }

  function difficultyInfo(d) {
    return DIFFICULTY[d] || UNKNOWN;
  }

  // Lajny jednoho parku + souhrn pro panel.
  // Pozor na počty: jedna lajna bývá v OSM rozdělená na víc úseků (way), proto se
  // úseky se stejným názvem a obtížností slučují do jedné lajny a jako „lajny“ se
  // počítají jen pojmenované. Úseky bez názvu jsou zvlášť a lesní / přístupové cesty
  // (access) do lajn nepatří vůbec.
  function summarize(fc, parkId) {
    const features = ((fc && fc.features) || []).filter((f) => f.properties && f.properties.park_id === parkId);
    const trails = features.filter((f) => !f.properties.access);
    const access = features.filter((f) => f.properties.access);
    const lengthM = trails.reduce((a, f) => a + (f.properties.length_m || 0), 0);
    const accessLengthM = access.reduce((a, f) => a + (f.properties.length_m || 0), 0);

    // Pojmenovaná lajna = jeden řádek bez ohledu na obtížnost úseků (OSM mívá krátký úsek
    // s jiným mtb:scale, např. nájezd); obtížnost řádku je ta s největší délkou.
    // Úseky bez názvu se slučují podle obtížnosti.
    // Název se porovnává bez ohledu na velikost písmen a mezery (OSM: „Stará Medvědice“
    // i „Stará medvědice“); zobrazí se pravopis nejdelšího úseku.
    const named = new Map(); // name nebo "|difficulty" → { name, difficulty, lengthM, segments, byDiff }
    for (const f of trails) {
      const p = f.properties;
      const key = p.name ? p.name.trim().toLowerCase().replace(/\s+/g, " ") : `|${p.difficulty || ""}`;
      const row = named.get(key) || { name: p.name || null, difficulty: p.difficulty || null, lengthM: 0, segments: 0, slopeMax: null, eleMin: null, eleMax: null, byDiff: {}, longestSeg: -1 };
      if ((p.length_m || 0) > row.longestSeg) { row.longestSeg = p.length_m || 0; row.name = p.name || null; }
      row.lengthM += p.length_m || 0;
      row.segments += 1;
      const d = p.difficulty || "";
      row.byDiff[d] = (row.byDiff[d] || 0) + (p.length_m || 0);
      row.difficulty = Object.entries(row.byDiff).sort((a, b) => b[1] - a[1])[0][0] || null;
      if (p.slope_max != null) row.slopeMax = Math.max(row.slopeMax ?? 0, p.slope_max);
      if (p.ele_min != null) row.eleMin = Math.min(row.eleMin ?? Infinity, p.ele_min);
      if (p.ele_max != null) row.eleMax = Math.max(row.eleMax ?? -Infinity, p.ele_max);
      named.set(key, row);
    }
    const rows = [...named.values()].map(({ byDiff, longestSeg, ...r }) => r).sort((a, b) =>
      ORDER.indexOf(a.difficulty) - ORDER.indexOf(b.difficulty)
      || (a.name === null) - (b.name === null)
      || (a.name || "").localeCompare(b.name || "", "cs"));

    const byDifficulty = {};
    for (const f of trails) {
      const d = f.properties.difficulty || "unknown";
      byDifficulty[d] = (byDifficulty[d] || 0) + (f.properties.length_m || 0);
    }
    const namedCount = rows.filter((r) => r.name).length;
    const sources = {}; // "osm" | "gpx" | "trailforks" → počet úseků
    for (const f of trails) {
      const src = f.properties.source || "osm";
      sources[src] = (sources[src] || 0) + 1;
    }
    const snapped = trails.filter((f) => f.properties.snapped).length; // průběh podle sítě Mapy.com
    const official = trails.filter((f) => f.properties.difficulty_source === "official").length; // barva podle značení parku
    // nejdelší lajna (pojmenovaná má přednost), celkové převýšení ze sklonových dat
    const longest = [...rows].sort((a, b) => (b.name !== null) - (a.name !== null) || b.lengthM - a.lengthM)[0] || null;
    const eles = rows.filter((r) => r.eleMin != null && r.eleMax != null);
    const eleMin = eles.length ? Math.min(...eles.map((r) => r.eleMin)) : null;
    const eleMax = eles.length ? Math.max(...eles.map((r) => r.eleMax)) : null;
    return {
      features, count: features.length, segments: trails.length, namedCount, lengthM, accessLengthM,
      rows, byDifficulty, sources, snapped, official, hasSlopes: hasSlopes(features), longest, eleMin, eleMax,
    };
  }

  // --- výškový profil lajny ---------------------------------------------------
  function distM(a, b) { // [lon, lat]
    const r = 6371000, rad = Math.PI / 180;
    const dLat = (b[1] - a[1]) * rad, dLon = (b[0] - a[0]) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * r * Math.asin(Math.sqrt(h));
  }

  // Úseky jedné lajny (mohou být rozházené a různě otočené) → body profilu v pořadí jízdy:
  // [{d (m od začátku), e (m n. m.), lon, lat}]. Směr dává nejdelší úsek (OSM i routing kreslí
  // lajny ve směru jízdy), ostatní se připojují k bližšímu konci; mezery mezi úseky se do
  // vzdálenosti nepočítají. Úseky bez výšek (eles) se vynechají. Vrací null, když nic nezbude.
  function profile(features) {
    const pieces = features
      .filter((f) => Array.isArray(f.properties.eles) && f.properties.eles.length === f.geometry.coordinates.length && f.geometry.coordinates.length > 1)
      .map((f) => f.geometry.coordinates.map((c, i) => ({ lon: c[0], lat: c[1], e: f.properties.eles[i] })));
    if (!pieces.length) return null;
    const len = (pts) => pts.reduce((a, p, i) => (i ? a + distM([pts[i - 1].lon, pts[i - 1].lat], [p.lon, p.lat]) : 0), 0);
    pieces.sort((a, b) => len(b) - len(a));
    let chain = pieces.shift().slice();
    const at = (p) => [p.lon, p.lat];
    while (pieces.length) {
      const head = at(chain[0]), tail = at(chain[chain.length - 1]);
      let best = null;
      pieces.forEach((pc, i) => {
        const s = at(pc[0]), e = at(pc[pc.length - 1]);
        for (const [d, how] of [[distM(tail, s), "tail"], [distM(tail, e), "tail-rev"], [distM(head, e), "head"], [distM(head, s), "head-rev"]]) {
          if (!best || d < best.d) best = { d, i, how };
        }
      });
      const pc = pieces.splice(best.i, 1)[0];
      if (best.how === "tail") chain = chain.concat(pc);
      else if (best.how === "tail-rev") chain = chain.concat(pc.slice().reverse());
      else if (best.how === "head") chain = pc.concat(chain);
      else chain = pc.slice().reverse().concat(chain);
    }
    let d = 0;
    return chain.map((p, i) => {
      if (i) {
        const step = distM(at(chain[i - 1]), at(p));
        if (step < 150) d += step; // mezera mezi úseky (> 150 m) se nepočítá
      }
      return { d, e: p.e, lon: p.lon, lat: p.lat };
    });
  }

  // Souhrn profilu: délka, stoupání a klesání (vyhlazené přes ~ smoothM, ať šum výškového
  // modelu nesčítá desítky metrů navíc), nejvyšší a nejnižší bod.
  function profileStats(pts, smoothM = 50) {
    if (!pts || pts.length < 2) return null;
    let up = 0, down = 0, last = pts[0];
    for (const p of pts) {
      if (p.d - last.d >= smoothM || p === pts[pts.length - 1]) {
        const dz = p.e - last.e;
        if (dz > 0) up += dz; else down -= dz;
        last = p;
      }
    }
    const es = pts.map((p) => p.e);
    return { lengthM: pts[pts.length - 1].d, up: Math.round(up), down: Math.round(down), min: Math.min(...es), max: Math.max(...es) };
  }

  const api = { DIFFICULTY, SLOPE_BINS, difficultyInfo, slopeBin, slopeRuns, hasSlopes, summarize, profile, profileStats };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShrederiqTrails = api;
})(typeof window !== "undefined" ? window : globalThis);
