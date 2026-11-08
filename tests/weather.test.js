const { test } = require("node:test");
const assert = require("node:assert/strict");
const W = require("../web/weather.js");

// Syntetická odpověď Open-Meteo: past_days=2 + 4 dny předpovědi, hodinově.
function fixture({ rainAt = {} } = {}) {
  const days = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"];
  const time = days.flatMap((d) => Array.from({ length: 24 }, (_, h) => `${d}T${String(h).padStart(2, "0")}:00`));
  return {
    elevation: 1123.4,
    current: { temperature_2m: 14.6, apparent_temperature: 12.2, precipitation: 0, weather_code: 2, wind_speed_10m: 11.4, wind_gusts_10m: 30.2, is_day: 1 },
    hourly: {
      time,
      temperature_2m: time.map((_, i) => i % 24),          // teplota = hodina dne
      precipitation: time.map((t) => rainAt[t] || 0),
      precipitation_probability: time.map((_, i) => (i % 3) * 10),
      weather_code: time.map((_, i) => (i % 24 === 13 ? 61 : 1)),
      wind_speed_10m: time.map(() => 10),
      is_day: time.map((_, i) => (i % 24 >= 6 && i % 24 < 21 ? 1 : 0)),
    },
    daily: {
      time: days,
      weather_code: [3, 3, 2, 61, 95, 0],
      temperature_2m_max: [20.4, 21, 22, 18.6, 25, 19],
      temperature_2m_min: [8, 9, 10, 7.4, 12, 6],
      precipitation_sum: [0, 0, 0, 4.26, 12, 0],
      precipitation_probability_max: [0, 0, 10, 70, 90, 5],
      wind_speed_10m_max: [15, 12, 10, 22.6, 40, 8],
    },
  };
}

// 1. 7. 2026 10:30 UTC = 12:30 v Praze (letní čas)
const NOW = new Date("2026-07-01T10:30:00Z");

test("URL obsahuje souřadnice, časovou zónu a historii srážek", () => {
  const u = new URL(W.forecastUrl(50.10612, 17.0981));
  assert.equal(u.origin + u.pathname, "https://api.open-meteo.com/v1/forecast");
  assert.equal(u.searchParams.get("latitude"), "50.1061");
  assert.equal(u.searchParams.get("timezone"), "Europe/Prague");
  assert.equal(u.searchParams.get("past_days"), "2");
  assert.equal(u.searchParams.get("forecast_days"), "4");
  assert.match(u.searchParams.get("hourly"), /precipitation_probability/);
});

test("lokální čas v Praze včetně letního / zimního času", () => {
  assert.equal(W.localKey(NOW), "2026-07-01T12:00");
  assert.equal(W.localKey(new Date("2026-01-15T23:30:00Z")), "2026-01-16T00:00");
});

test("kódy počasí: den / noc a neznámý kód", () => {
  assert.deepEqual(W.describeCode(0, 1), { icon: "sun", label: "jasno" });
  assert.equal(W.describeCode(0, 0).icon, "moon");
  assert.equal(W.describeCode(95).label, "bouřka");
  assert.equal(W.describeCode(999).label, "neznámé");
});

test("hodinová předpověď začíná aktuální hodinou a má 24 hodin", () => {
  const h = W.hourlyBlocks(fixture().hourly, NOW, 1);
  assert.equal(h.length, 24);
  assert.equal(h[0].time, "2026-07-01T12:00");
  assert.equal(h[0].temperature, 12);
  assert.equal(h[1].label, "déšť"); // 13:00 má kód 61
  assert.equal(h[23].time, "2026-07-02T11:00");
});

test("bloky po 3 hodinách: průměr teploty, součet srážek, nejhorší počasí", () => {
  const data = fixture({ rainAt: { "2026-07-01T13:00": 1.2, "2026-07-01T14:00": 0.5 } });
  const h = W.hourlyBlocks(data.hourly, NOW, 3);
  assert.equal(h.length, 8);
  assert.equal(h[0].time, "2026-07-01T12:00");
  assert.equal(h[0].temperature, 13);       // (12 + 13 + 14) / 3
  assert.equal(h[0].precipitation, 1.7);
  assert.equal(h[0].label, "déšť");          // max(1, 61, 1)
  assert.equal(h[0].precipitationProbability, 20);
  assert.equal(h[1].time, "2026-07-01T15:00");
});

test("výhled na 3 dny po dnešku", () => {
  const d = W.dailyOutlook(fixture().daily, NOW);
  assert.deepEqual(d.map((x) => x.date), ["2026-07-02", "2026-07-03", "2026-07-04"]);
  assert.equal(d[0].weekday, "čt");
  assert.equal(d[0].min, 7);
  assert.equal(d[0].max, 19);
  assert.equal(d[0].precipitation, 4.3);
  assert.equal(d[1].label, "bouřka");
});

test("stav trailů podle srážek za posledních 48 hodin", () => {
  assert.equal(W.trailCondition(fixture().hourly, NOW).level, "dry");
  let t = W.trailCondition(fixture({ rainAt: { "2026-06-30T20:00": 3 } }).hourly, NOW);
  assert.equal(t.level, "damp");
  assert.equal(t.last24, 3);
  t = W.trailCondition(fixture({ rainAt: { "2026-07-01T08:00": 8, "2026-07-01T09:00": 4 } }).hourly, NOW);
  assert.equal(t.level, "wet");
  assert.equal(t.hours, 48);
  // déšť před víc než 48 hodinami se nepočítá
  t = W.trailCondition(fixture({ rainAt: { "2026-06-29T10:00": 30 } }).hourly, NOW);
  assert.equal(t.level, "dry");
});

test("souhrn pro panel", () => {
  const s = W.summarize(fixture(), NOW, 3);
  assert.equal(s.current.temperature, 15);
  assert.equal(s.current.label, "polojasno");
  assert.equal(s.current.windGusts, 30);
  assert.equal(s.hourly.length, 8);
  assert.equal(s.daily.length, 3);
  assert.equal(s.elevation, 1123);
});

test("každý kód počasí má ikonu ze sady icons.js", () => {
  const { names } = require("../web/icons.js");
  for (let code = 0; code < 100; code++) {
    for (const day of [0, 1]) assert.ok(names.includes(W.describeCode(code, day).icon), `kód ${code}, den ${day}`);
  }
});
