const { test } = require("node:test");
const assert = require("node:assert/strict");
const N = require("../web/nav.js");

const park = (id, lon, lat, extra = {}) => ({
  type: "Feature",
  geometry: { type: "Point", coordinates: [lon, lat] },
  properties: { id, name: id, kind: "bikepark", ...extra },
});
const PRAHA = { lat: 50.0755, lon: 14.4378 };

test("cíl je parkoviště, pokud ho park má", () => {
  assert.deepEqual(N.destination(park("a", 17.1, 50.1)), [17.1, 50.1]);
  assert.deepEqual(N.destination(park("a", 17.1, 50.1, { parking: [17.09, 50.11] })), [17.09, 50.11]);
});

test("odkazy do Mapy.com a Google Maps", () => {
  let l = N.navLinks([17.1, 50.1]);
  assert.equal(new URL(l.mapy).searchParams.get("end"), "17.1,50.1");
  assert.equal(new URL(l.mapy).searchParams.get("start"), null);
  assert.equal(new URL(l.google).searchParams.get("destination"), "50.1,17.1");
  l = N.navLinks([17.1, 50.1], PRAHA);
  assert.equal(new URL(l.mapy).searchParams.get("start"), "14.4378,50.0755");
  assert.equal(new URL(l.google).searchParams.get("origin"), "50.0755,14.4378");
});

test("URL routing API", () => {
  const u = new URL(N.routeUrl(PRAHA, [17.1, 50.1], "KEY"));
  assert.equal(u.origin + u.pathname, "https://api.mapy.com/v1/routing/route");
  assert.equal(u.searchParams.get("start"), "14.4378,50.0755");
  assert.equal(u.searchParams.get("end"), "17.1,50.1");
  assert.equal(u.searchParams.get("format"), "geojson");
  assert.equal(u.searchParams.get("apikey"), "KEY");
});

test("parsování odpovědi routing API (Feature i holá geometrie)", () => {
  const line = { type: "LineString", coordinates: [[14.4, 50.0], [17.1, 50.1]] };
  let r = N.parseRoute({ length: 251300, duration: 9420, geometry: { type: "Feature", geometry: line, properties: {} } });
  assert.deepEqual(r, { lengthM: 251300, durationS: 9420, coordinates: line.coordinates });
  r = N.parseRoute({ length: 1, duration: 2, geometry: line });
  assert.equal(r.coordinates.length, 2);
  assert.throws(() => N.parseRoute({ error: "x" }));
});

test("formátování vzdálenosti a času", () => {
  assert.equal(N.formatDistance(456), "460 m");
  assert.equal(N.formatDistance(4560), "4,6 km");
  assert.equal(N.formatDistance(251300), "251 km");
  assert.equal(N.formatDuration(20), "1 min");
  assert.equal(N.formatDuration(45 * 60), "45 min");
  assert.equal(N.formatDuration(9420), "2 h 37 min");
  assert.equal(N.formatDuration(7200), "2 h");
});

test("řazení parků podle vzdálenosti", () => {
  const list = N.byDistance([park("kouty", 17.098, 50.106), park("spicak", 13.226, 49.163), park("jested", 14.985, 50.732)], PRAHA);
  assert.deepEqual(list.map((x) => x.feature.properties.id), ["jested", "spicak", "kouty"]);
  assert.ok(Math.abs(list[0].distanceM - 85000) < 5000);
});
