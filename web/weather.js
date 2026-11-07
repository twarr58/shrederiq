// Počasí z Open-Meteo (https://open-meteo.com/, CC BY 4.0) – čisté funkce bez DOM,
// testuje je tests/weather.test.js.
(function (root) {
  "use strict";

  const API = "https://api.open-meteo.com/v1/forecast";
  const HOURS_AHEAD = 24;
  const DAYS_AHEAD = 3;
  const PAST_HOURS = 48; // pro odhad, jak moc je na trailech mokro

  const CURRENT = ["temperature_2m", "apparent_temperature", "precipitation", "weather_code", "wind_speed_10m", "wind_gusts_10m", "is_day"];
  const HOURLY = ["temperature_2m", "precipitation", "precipitation_probability", "weather_code", "wind_speed_10m", "is_day"];
  const DAILY = ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum", "precipitation_probability_max", "wind_speed_10m_max"];

  function forecastUrl(lat, lon) {
    const q = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      current: CURRENT.join(","),
      hourly: HOURLY.join(","),
      daily: DAILY.join(","),
      timezone: "Europe/Prague",
      past_days: "2",
      forecast_days: String(DAYS_AHEAD + 1),
    });
    return `${API}?${q}`;
  }

  // WMO weather codes → ikona + český popis (https://open-meteo.com/en/docs, sekce WMO)
  const WMO = [
    [[0], "☀️", "🌙", "jasno"],
    [[1], "🌤️", "🌙", "skoro jasno"],
    [[2], "⛅", "☁️", "polojasno"],
    [[3], "☁️", "☁️", "zataženo"],
    [[45, 48], "🌫️", "🌫️", "mlha"],
    [[51, 53, 55], "🌦️", "🌧️", "mrholení"],
    [[56, 57], "🌧️", "🌧️", "mrznoucí mrholení"],
    [[61, 63], "🌧️", "🌧️", "déšť"],
    [[65], "🌧️", "🌧️", "silný déšť"],
    [[66, 67], "🌧️", "🌧️", "mrznoucí déšť"],
    [[71, 73, 75, 77], "🌨️", "🌨️", "sněžení"],
    [[80, 81], "🌦️", "🌧️", "přeháňky"],
    [[82], "⛈️", "⛈️", "silné přeháňky"],
    [[85, 86], "🌨️", "🌨️", "sněhové přeháňky"],
    [[95], "⛈️", "⛈️", "bouřka"],
    [[96, 99], "⛈️", "⛈️", "bouřka s krupobitím"],
  ];

  function describeCode(code, isDay = 1) {
    const row = WMO.find(([codes]) => codes.includes(code));
    if (!row) return { icon: "❔", label: "neznámé" };
    return { icon: isDay ? row[1] : row[2], label: row[3] };
  }

  // Index první hodiny v hourly.time, která je >= nyní (časy jsou lokální "YYYY-MM-DDTHH:MM").
  function hourIndex(times, now) {
    const key = localKey(now);
    const i = times.findIndex((t) => t >= key);
    return i === -1 ? times.length : i;
  }

  // Lokální čas v Praze jako "YYYY-MM-DDTHH:00" – stejný formát, jaký vrací Open-Meteo.
  function localKey(date) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Prague", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
    }).formatToParts(date).map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:00`;
  }

  // Hodinová předpověď od teď na HOURS_AHEAD hodin; step=3 → bloky po 3 hodinách.
  function hourlyBlocks(hourly, now, step = 1) {
    const start = hourIndex(hourly.time, now);
    const end = Math.min(start + HOURS_AHEAD, hourly.time.length);
    const blocks = [];
    for (let i = start; i < end; i += step) {
      const idx = [];
      for (let j = i; j < Math.min(i + step, end); j++) idx.push(j);
      const pick = (k) => idx.map((j) => hourly[k][j]).filter((v) => v != null);
      // pro blok: kód počasí nejhoršího stavu (vyšší WMO kód = horší), teplota průměr, srážky součet
      const codes = pick("weather_code");
      const temps = pick("temperature_2m");
      blocks.push({
        time: hourly.time[i],
        hour: Number(hourly.time[i].slice(11, 13)),
        temperature: temps.length ? round1(temps.reduce((a, b) => a + b, 0) / temps.length) : null,
        precipitation: round1(pick("precipitation").reduce((a, b) => a + b, 0)),
        precipitationProbability: Math.max(0, ...pick("precipitation_probability")),
        windSpeed: Math.max(0, ...pick("wind_speed_10m")),
        ...describeCode(codes.length ? Math.max(...codes) : null, hourly.is_day ? hourly.is_day[i] : 1),
      });
    }
    return blocks;
  }

  // Výhled na DAYS_AHEAD dní po dnešku.
  function dailyOutlook(daily, now) {
    const today = localKey(now).slice(0, 10);
    const start = daily.time.findIndex((d) => d > today);
    if (start === -1) return [];
    return daily.time.slice(start, start + DAYS_AHEAD).map((date, k) => {
      const i = start + k;
      return {
        date,
        weekday: new Intl.DateTimeFormat("cs-CZ", { weekday: "short", timeZone: "UTC" }).format(new Date(`${date}T12:00:00Z`)),
        min: Math.round(daily.temperature_2m_min[i]),
        max: Math.round(daily.temperature_2m_max[i]),
        precipitation: round1(daily.precipitation_sum[i]),
        precipitationProbability: daily.precipitation_probability_max[i],
        windMax: Math.round(daily.wind_speed_10m_max[i]),
        ...describeCode(daily.weather_code[i], 1),
      };
    });
  }

  // Srážky za posledních PAST_HOURS hodin a hrubý odhad stavu trailů.
  function trailCondition(hourly, now) {
    const end = hourIndex(hourly.time, now);
    const start = Math.max(0, end - PAST_HOURS);
    const values = hourly.precipitation.slice(start, end).filter((v) => v != null);
    const last24 = values.slice(-24).reduce((a, b) => a + b, 0);
    const total = values.reduce((a, b) => a + b, 0);
    let level, label;
    if (last24 >= 10 || total >= 20) [level, label] = ["wet", "mokro, počítej s blátem"];
    else if (last24 >= 2 || total >= 5) [level, label] = ["damp", "vlhko, místy kluzko"];
    else [level, label] = ["dry", "sucho"];
    return { hours: values.length, precipitation: round1(total), last24: round1(last24), level, label };
  }

  function summarize(data, now = new Date(), step = 1) {
    const c = data.current || {};
    return {
      current: {
        temperature: Math.round(c.temperature_2m),
        apparent: Math.round(c.apparent_temperature),
        precipitation: c.precipitation,
        windSpeed: Math.round(c.wind_speed_10m),
        windGusts: Math.round(c.wind_gusts_10m),
        ...describeCode(c.weather_code, c.is_day),
      },
      hourly: hourlyBlocks(data.hourly, now, step),
      daily: dailyOutlook(data.daily, now),
      trail: trailCondition(data.hourly, now),
      elevation: data.elevation != null ? Math.round(data.elevation) : null,
    };
  }

  function round1(x) { return Math.round(x * 10) / 10; }

  const api = { forecastUrl, describeCode, localKey, hourlyBlocks, dailyOutlook, trailCondition, summarize, HOURS_AHEAD, DAYS_AHEAD };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShrederiqWeather = api;
})(typeof window !== "undefined" ? window : globalThis);
