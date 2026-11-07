// Čisté funkce nad daty parků (bez DOM / Leafletu) – testuje je tests/parks.test.js.
(function (root) {
  "use strict";

  const KINDS = { bikepark: "Bikepark", trail_center: "Trail centrum" };

  // přibližný obdélník ČR – hrubá kontrola prohozených / nesmyslných souřadnic
  const CZ_BOUNDS = { minLon: 12.09, maxLon: 18.86, minLat: 48.55, maxLat: 51.06 };

  const ID_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

  // "Špičák" → "spicak": hledání bez ohledu na diakritiku a velikost písmen
  function fold(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  }

  // Vrátí seznam chyb (prázdný = data jsou v pořádku).
  function validateParks(fc) {
    const errors = [];
    if (!fc || fc.type !== "FeatureCollection" || !Array.isArray(fc.features)) {
      return ["očekávám GeoJSON FeatureCollection"];
    }
    const seen = new Set();
    fc.features.forEach((f, i) => {
      const p = (f && f.properties) || {};
      const at = `#${i} (${p.id || "?"})`;
      if (!f || f.type !== "Feature") errors.push(`${at}: není Feature`);
      const g = f && f.geometry;
      if (!g || g.type !== "Point" || !Array.isArray(g.coordinates) || g.coordinates.length < 2) {
        errors.push(`${at}: geometrie musí být Point`);
      } else {
        const [lon, lat] = g.coordinates;
        if (!(lon >= CZ_BOUNDS.minLon && lon <= CZ_BOUNDS.maxLon && lat >= CZ_BOUNDS.minLat && lat <= CZ_BOUNDS.maxLat)) {
          errors.push(`${at}: souřadnice [${lon}, ${lat}] leží mimo ČR (pořadí je [lon, lat])`);
        }
      }
      if (typeof p.id !== "string" || !ID_RE.test(p.id)) errors.push(`${at}: id musí být kebab-case`);
      else if (seen.has(p.id)) errors.push(`${at}: duplicitní id`);
      else seen.add(p.id);
      if (typeof p.name !== "string" || !p.name.trim()) errors.push(`${at}: chybí name`);
      if (!(p.kind in KINDS)) errors.push(`${at}: kind musí být ${Object.keys(KINDS).join(" | ")}`);
      if (p.web != null && !/^https:\/\//.test(p.web)) errors.push(`${at}: web musí začínat https://`);
      if (p.parking != null) {
        const [plon, plat] = Array.isArray(p.parking) ? p.parking : [];
        if (!(plon >= CZ_BOUNDS.minLon && plon <= CZ_BOUNDS.maxLon && plat >= CZ_BOUNDS.minLat && plat <= CZ_BOUNDS.maxLat)) {
          errors.push(`${at}: parking musí být [lon, lat] v ČR`);
        }
      }
      if (p.lift != null && typeof p.lift !== "boolean") errors.push(`${at}: lift musí být true / false / null`);
      if (p.trails_count != null && !(Number.isInteger(p.trails_count) && p.trails_count >= 0)) {
        errors.push(`${at}: trails_count musí být nezáporné celé číslo nebo null`);
      }
    });
    return errors;
  }

  // Filtr pro vyhledávací pole: název, obec i kraj, bez diakritiky.
  function filterParks(features, query) {
    const q = fold(query);
    const list = q
      ? features.filter((f) => {
          const p = f.properties;
          return [p.name, p.town, p.region].some((v) => fold(v).includes(q));
        })
      : features.slice();
    return list.sort((a, b) => a.properties.name.localeCompare(b.properties.name, "cs"));
  }

  // Řádky info panelu; neznámé hodnoty (null) se zobrazí jako „neověřeno“.
  function describePark(p) {
    const unknown = "neověřeno";
    return [
      ["Typ", KINDS[p.kind] || p.kind],
      ["Lokalita", [p.town, p.region && `${p.region} kraj`].filter(Boolean).join(", ") || unknown],
      ["Lanovka / vlek", p.lift == null ? unknown : p.lift ? "ano" : "ne"],
      ["Počet trailů", p.trails_count == null ? unknown : String(p.trails_count)],
    ];
  }

  const api = { KINDS, CZ_BOUNDS, fold, validateParks, filterParks, describePark };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShrederiqParks = api;
})(typeof window !== "undefined" ? window : globalThis);
