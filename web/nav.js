// Navigace do parku – čisté funkce bez DOM, testuje je tests/nav.test.js.
(function (root) {
  "use strict";

  const MAPY_ROUTING = "https://api.mapy.com/v1/routing/route";

  function haversineM(lat1, lon1, lat2, lon2) {
    const r = 6371000, rad = Math.PI / 180;
    const dp = (lat2 - lat1) * rad, dl = (lon2 - lon1) * rad;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dl / 2) ** 2;
    return 2 * r * Math.asin(Math.sqrt(a));
  }

  // Cíl navigace: parkoviště, když ho park má, jinak bod parku. Vrací [lon, lat].
  function destination(feature) {
    const p = feature.properties || {};
    return Array.isArray(p.parking) && p.parking.length >= 2 ? p.parking : feature.geometry.coordinates;
  }

  // Odkazy do navigačních aplikací (fungují bez API klíče, na mobilu otevřou aplikaci).
  function navLinks([lon, lat], from) {
    const mapy = new URL("https://mapy.com/fnc/v1/route");
    mapy.searchParams.set("mapset", "traffic");
    mapy.searchParams.set("end", `${lon},${lat}`);
    if (from) mapy.searchParams.set("start", `${from.lon},${from.lat}`);
    mapy.searchParams.set("routeType", "car_fast");

    const google = new URL("https://www.google.com/maps/dir/");
    google.searchParams.set("api", "1");
    google.searchParams.set("destination", `${lat},${lon}`);
    if (from) google.searchParams.set("origin", `${from.lat},${from.lon}`);
    google.searchParams.set("travelmode", "driving");

    return { mapy: mapy.toString(), google: google.toString() };
  }

  function routeUrl(from, [lon, lat], apiKey) {
    const u = new URL(MAPY_ROUTING);
    u.searchParams.set("start", `${from.lon},${from.lat}`);
    u.searchParams.set("end", `${lon},${lat}`);
    u.searchParams.set("routeType", "car_fast");
    u.searchParams.set("format", "geojson");
    u.searchParams.set("lang", "cs");
    u.searchParams.set("apikey", apiKey);
    return u.toString();
  }

  // Odpověď Mapy.com routing API → { lengthM, durationS, coordinates: [[lon, lat], …] }
  function parseRoute(res) {
    if (!res || typeof res.length !== "number" || typeof res.duration !== "number") {
      throw new Error("neočekávaná odpověď routing API");
    }
    let g = res.geometry;
    if (g && g.type === "Feature") g = g.geometry;
    const coordinates = g && g.type === "LineString" ? g.coordinates : [];
    return { lengthM: res.length, durationS: res.duration, coordinates };
  }

  function formatDistance(m) {
    if (m < 1000) return `${Math.round(m / 10) * 10} m`;
    if (m < 10000) return `${(m / 1000).toFixed(1).replace(".", ",")} km`;
    return `${Math.round(m / 1000)} km`;
  }

  function formatDuration(s) {
    const min = Math.max(1, Math.round(s / 60));
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60), rest = min % 60;
    return rest ? `${h} h ${rest} min` : `${h} h`;
  }

  // Vzdušná vzdálenost od uživatele ke každému parku, seřazeno od nejbližšího.
  function byDistance(features, from) {
    return features
      .map((f) => {
        const [lon, lat] = f.geometry.coordinates;
        return { feature: f, distanceM: haversineM(from.lat, from.lon, lat, lon) };
      })
      .sort((a, b) => a.distanceM - b.distanceM);
  }

  const api = { haversineM, destination, navLinks, routeUrl, parseRoute, formatDistance, formatDuration, byDistance };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShrederiqNav = api;
})(typeof window !== "undefined" ? window : globalThis);
