(function () {
  "use strict";

  const cfg = window.SHREDERIQ_CONFIG || {};
  const P = window.ShrederiqParks;
  const W = window.ShrederiqWeather;
  const N = window.ShrederiqNav;
  const CZ_CENTER = [49.8, 15.5];
  const PARK_ZOOM = 14;

  const $ = (id) => document.getElementById(id);
  const num = (x) => String(x).replace(".", ","); // české desetinné číslo
  const el = (tag, props, ...children) => {
    const n = Object.assign(document.createElement(tag), props || {});
    n.append(...children);
    return n;
  };

  // --- mapa a podklad --------------------------------------------------------
  const map = L.map("map", { zoomControl: false }).setView(CZ_CENTER, 7);
  L.control.zoom({ position: "bottomright" }).addTo(map);

  // Podklad: OpenStreetMap, nebo Mapy.com (s klíčem). Mapy.com mají limit 100k dlaždic
  // měsíčně, proto: přehled ČR z OSM a Mapy.com až od zoomu mapyMinZoom, dlaždice se
  // nenačítají během zoomu a při chybách Mapy.com (vyčerpaný limit) se přepne na OSM.
  const TILE_OPTS = { maxZoom: 19, updateWhenZooming: false, updateWhenIdle: true, keepBuffer: 1 };
  const MAPY_MIN_ZOOM = Number.isFinite(cfg.mapyMinZoom) ? cfg.mapyMinZoom : 11;
  const MAPY_MAX_ERRORS = 8;

  const osmLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    ...TILE_OPTS,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
  });
  let mapyLayer = null, mapyLogo = null, mapyErrors = 0, mapyFailed = false;
  let base = "osm";

  if (cfg.mapyApiKey) {
    // Mapy.com – podmínky vyžadují atribuci i logo (https://developer.mapy.com/)
    const mapset = cfg.mapyMapset || "outdoor";
    mapyLayer = L.tileLayer(`https://api.mapy.com/v1/maptiles/${mapset}/256/{z}/{x}/{y}?apikey=${encodeURIComponent(cfg.mapyApiKey)}`, {
      ...TILE_OPTS,
      attribution: '<a href="https://api.mapy.com/copyright" target="_blank" rel="noopener">&copy; Seznam.cz a.s. a další</a>',
    });
    mapyLayer.on("tileerror", () => {
      if (++mapyErrors >= MAPY_MAX_ERRORS && !mapyFailed) {
        mapyFailed = true;
        console.warn("Mapy.com tiles failing, switching to OpenStreetMap");
        updateBase();
      }
    });
    mapyLayer.on("tileload", () => { mapyErrors = 0; });
    const Logo = L.Control.extend({
      options: { position: "bottomleft" },
      onAdd: () => el("a", { className: "mapy-logo", href: "https://mapy.com/", target: "_blank", rel: "noopener" },
        el("img", { src: "https://api.mapy.com/img/api/logo.svg", alt: "Mapy.com" })),
    });
    mapyLogo = new Logo();

    try { base = localStorage.getItem("shrederiq.base") === "osm" ? "osm" : "mapy"; } catch (_) { base = "mapy"; }
    const choices = { "Mapy.com (turistická)": L.layerGroup(), "OpenStreetMap": L.layerGroup() };
    (base === "mapy" ? choices["Mapy.com (turistická)"] : choices.OpenStreetMap).addTo(map);
    L.control.layers(choices, null, { position: "bottomright" }).addTo(map);
    map.on("baselayerchange", (e) => {
      base = e.layer === choices.OpenStreetMap ? "osm" : "mapy";
      try { localStorage.setItem("shrederiq.base", base); } catch (_) { /* bez storage */ }
      updateBase();
    });
  }

  function updateBase() {
    const useMapy = mapyLayer && base === "mapy" && !mapyFailed && map.getZoom() >= MAPY_MIN_ZOOM;
    const [on, off] = useMapy ? [mapyLayer, osmLayer] : [osmLayer, mapyLayer];
    if (off && map.hasLayer(off)) map.removeLayer(off);
    if (!map.hasLayer(on)) on.addTo(map);
    if (mapyLogo) useMapy ? mapyLogo.addTo(map) : mapyLogo.remove();
  }
  map.on("zoomend", updateBase);
  updateBase();

  // --- stav ------------------------------------------------------------------
  let parks = [];           // GeoJSON features
  const markers = new Map(); // id → L.circleMarker
  let activeId = null;
  let userPos = null;        // { lat, lon } po kliknutí na „Moje poloha“
  let userMarker = null;
  let routeLayer = null;

  const color = (kind) => getComputedStyle(document.documentElement).getPropertyValue(`--${kind}`).trim() || "#e4572e";

  function renderList() {
    let list = P.filterParks(parks, $("search").value);
    const dist = new Map();
    if (userPos) {
      list = N.byDistance(list, userPos).map(({ feature, distanceM }) => (dist.set(feature, distanceM), feature));
    }
    const items = list.map((f) => {
      const p = f.properties;
      const where = [p.town, p.region].filter(Boolean).join(", ");
      const li = el("li", { className: p.id === activeId ? "active" : "" },
        el("i", { className: `dot ${p.kind}` }), " ", p.name,
        el("small", {}, dist.has(f) ? `${N.formatDistance(dist.get(f))} vzdušně · ${where}` : where));
      li.addEventListener("click", () => selectPark(p.id));
      return li;
    });
    $("list").replaceChildren(...items);
    $("status").textContent = `${items.length} z ${parks.length} parků`;
  }

  function renderCard(p) {
    const dl = el("dl");
    for (const [k, v] of P.describePark(p)) dl.append(el("dt", {}, k), el("dd", {}, v));
    if (p.web) dl.append(el("dt", {}, "Web"), el("dd", {}, el("a", { href: p.web, target: "_blank", rel: "noopener" }, new URL(p.web).hostname)));

    const close = el("button", { className: "close", type: "button", title: "Zavřít", ariaLabel: "Zavřít" }, "×");
    close.addEventListener("click", () => selectPark(null));

    const notes = [el("p", { className: "note" }, "Trasy jednotlivých lajn zatím nejsou k dispozici – doplníme z Trailforks / OSM.")];
    if (p.verified === false) notes.push(el("p", { className: "note" }, "Údaje o parku (včetně polohy) jsou orientační a čekají na ověření."));

    const weather = el("section", { className: "weather" }, el("p", { className: "muted" }, "Načítám počasí…"));
    $("card").replaceChildren(close, el("h2", {}, p.name), dl, renderNav(p.id), weather, ...notes);
    $("card").hidden = false;
    $("card").scrollTop = 0;
    loadWeather(p.id, weather);
  }

  // --- navigace ---------------------------------------------------------------
  function renderNav(id) {
    const f = parks.find((x) => x.properties.id === id);
    const dest = N.destination(f);
    const links = N.navLinks(dest, userPos);
    const box = el("section", { className: "nav" },
      el("a", { className: "btn primary", href: links.mapy, target: "_blank", rel: "noopener" }, "Navigovat (Mapy.com)"),
      el("a", { className: "btn", href: links.google, target: "_blank", rel: "noopener" }, "Google Maps"));
    const info = el("p", { className: "nav-info muted" });
    if (userPos) {
      const [lon, lat] = dest;
      info.textContent = `Vzdušně ${N.formatDistance(N.haversineM(userPos.lat, userPos.lon, lat, lon))} od tebe.`;
    }
    if (cfg.mapyApiKey) {
      const b = el("button", { className: "btn", type: "button" }, "Trasa v mapě");
      b.addEventListener("click", () => showRoute(id, info, b));
      box.append(b);
    }
    box.append(info);
    return box;
  }

  async function showRoute(id, info, button) {
    button.disabled = true;
    try {
      if (!userPos) await locate();
      info.textContent = "Hledám trasu…";
      const f = parks.find((x) => x.properties.id === id);
      const res = await fetch(N.routeUrl(userPos, N.destination(f), cfg.mapyApiKey));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const r = N.parseRoute(await res.json());
      if (activeId !== id) return;
      clearRoute();
      routeLayer = L.geoJSON({ type: "LineString", coordinates: r.coordinates }, { style: { color: "#3a86ff", weight: 5, opacity: 0.8 } }).addTo(map);
      map.fitBounds(routeLayer.getBounds(), { padding: [40, 40] });
      info.textContent = `Autem ${N.formatDistance(r.lengthM)}, asi ${N.formatDuration(r.durationS)}.`;
    } catch (err) {
      console.warn("route", err);
      info.textContent = err.code === 1 ? "Bez přístupu k poloze trasu nespočítám." : "Trasu se nepodařilo spočítat.";
    } finally {
      button.disabled = false;
    }
  }

  function clearRoute() {
    if (routeLayer) map.removeLayer(routeLayer);
    routeLayer = null;
  }

  function locate() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(Object.assign(new Error("geolocation unsupported"), { code: 2 }));
      navigator.geolocation.getCurrentPosition((pos) => {
        userPos = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        if (!userMarker) {
          userMarker = L.circleMarker([userPos.lat, userPos.lon], { radius: 7, weight: 3, color: "#fff", fillColor: "#3a86ff", fillOpacity: 1 })
            .bindTooltip("Tady jsi", { direction: "top" }).addTo(map);
        } else {
          userMarker.setLatLng([userPos.lat, userPos.lon]);
        }
        renderList();
        resolve(userPos);
      }, reject, { enableHighAccuracy: false, timeout: 15000, maximumAge: 5 * 60 * 1000 });
    });
  }

  $("locate").addEventListener("click", async () => {
    const b = $("locate");
    b.disabled = true;
    try {
      await locate();
      if (!activeId) map.setView([userPos.lat, userPos.lon], 9);
      else renderCard(parks.find((x) => x.properties.id === activeId).properties); // odkazy s výchozím bodem
      $("status").textContent += " · seřazeno podle vzdálenosti";
    } catch (err) {
      $("status").textContent = err.code === 1 ? "Přístup k poloze je zakázaný." : "Polohu se nepodařilo zjistit.";
    } finally {
      b.disabled = false;
    }
  });

  // --- počasí (Open-Meteo) ---------------------------------------------------
  const WEATHER_TTL_MS = 30 * 60 * 1000;
  const weatherCache = new Map(); // id → { at, data }
  let weatherStep = 1;
  try { weatherStep = Number(localStorage.getItem("shrederiq.weatherStep")) === 3 ? 3 : 1; } catch (_) { /* bez storage */ }

  async function loadWeather(id, box) {
    const f = parks.find((x) => x.properties.id === id);
    const [lon, lat] = f.geometry.coordinates;
    try {
      let hit = weatherCache.get(id);
      if (!hit || Date.now() - hit.at > WEATHER_TTL_MS) {
        const res = await fetch(W.forecastUrl(lat, lon));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        hit = { at: Date.now(), data: await res.json() };
        weatherCache.set(id, hit);
      }
      if (activeId === id && box.isConnected) renderWeather(box, hit.data);
    } catch (err) {
      console.warn("weather", err);
      if (box.isConnected) box.replaceChildren(el("p", { className: "muted" }, "Počasí se teď nepodařilo načíst."));
    }
  }

  function renderWeather(box, data) {
    const s = W.summarize(data, new Date(), weatherStep);
    const c = s.current;

    const now = el("div", { className: "wx-now" },
      el("span", { className: "wx-icon", title: c.label }, c.icon),
      el("span", { className: "wx-temp" }, `${c.temperature} °C`),
      el("span", { className: "muted" }, `${c.label}, pocitově ${c.apparent} °C`, el("br"),
        `vítr ${c.windSpeed} km/h, nárazy ${c.windGusts} km/h`));

    const trail = el("p", { className: `wx-trail ${s.trail.level}` },
      `Trailové podmínky: ${s.trail.label} `,
      el("small", {}, `(${num(s.trail.precipitation)} mm za ${s.trail.hours} h)`));

    const toggle = el("div", { className: "wx-toggle", role: "group", ariaLabel: "Krok předpovědi" },
      ...[1, 3].map((step) => {
        const b = el("button", { type: "button", className: step === weatherStep ? "on" : "" }, step === 1 ? "po hodině" : "po 3 h");
        b.addEventListener("click", () => {
          weatherStep = step;
          try { localStorage.setItem("shrederiq.weatherStep", String(step)); } catch (_) { /* bez storage */ }
          renderWeather(box, data);
        });
        return b;
      }));

    const hourly = el("div", { className: "wx-hours" }, ...s.hourly.map((h) =>
      el("div", { className: "wx-hour", title: `${h.label}, srážky ${num(h.precipitation)} mm (${h.precipitationProbability} %), vítr ${h.windSpeed} km/h` },
        el("small", {}, `${h.hour}:00`),
        el("span", { className: "wx-icon" }, h.icon),
        el("b", {}, `${Math.round(h.temperature)}°`),
        el("small", { className: h.precipitation > 0 ? "rain" : "muted" }, h.precipitation > 0 ? `${num(h.precipitation)} mm` : `${h.precipitationProbability} %`))));

    const daily = el("div", { className: "wx-days" }, ...s.daily.map((d) =>
      el("div", { className: "wx-day", title: `${d.label}, vítr až ${d.windMax} km/h` },
        el("b", {}, d.weekday),
        el("span", { className: "wx-icon" }, d.icon),
        el("span", {}, `${d.min}° / ${d.max}°`),
        el("small", { className: d.precipitation > 0 ? "rain" : "muted" }, `${num(d.precipitation)} mm · ${d.precipitationProbability ?? 0} %`))));

    box.replaceChildren(
      el("h3", {}, "Počasí", s.elevation != null ? el("small", { className: "muted" }, ` · ${s.elevation} m n. m.`) : ""),
      now, trail,
      el("div", { className: "wx-head" }, el("h4", {}, "Příštích 24 hodin"), toggle), hourly,
      el("h4", {}, "Další 3 dny"), daily,
      el("p", { className: "attr" }, "Předpověď: ", el("a", { href: "https://open-meteo.com/", target: "_blank", rel: "noopener" }, "Open-Meteo"), " (CC BY 4.0)"));
  }

  function selectPark(id, { fly = true } = {}) {
    const f = parks.find((x) => x.properties.id === id);
    if (activeId && markers.has(activeId)) markers.get(activeId).setStyle({ weight: 2, radius: 7 });
    activeId = f ? id : null;

    if (f) {
      markers.get(id).setStyle({ weight: 4, radius: 10 }).bringToFront();
      const [lon, lat] = f.geometry.coordinates;
      if (fly) map.setView([lat, lon], PARK_ZOOM); // bez flyTo: nenačítá dlaždice mezilehlých zoomů
      renderCard(f.properties);
      history.replaceState(null, "", `#${id}`);
    } else {
      clearRoute();
      $("card").hidden = true;
      history.replaceState(null, "", location.pathname + location.search);
    }
    renderList();
  }

  // --- data ------------------------------------------------------------------
  async function load() {
    const res = await fetch("data/parks.geojson", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const fc = await res.json();
    const errors = P.validateParks(fc);
    if (errors.length) console.warn("parks.geojson:", errors);
    parks = fc.features;

    for (const f of parks) {
      const p = f.properties;
      const [lon, lat] = f.geometry.coordinates;
      const m = L.circleMarker([lat, lon], { radius: 7, weight: 2, color: "#fff", fillColor: color(p.kind), fillOpacity: 0.95 })
        .bindTooltip(p.name, { direction: "top", offset: [0, -6] })
        .on("click", () => selectPark(p.id))
        .addTo(map);
      markers.set(p.id, m);
    }

    renderList();
    const fromHash = decodeURIComponent(location.hash.slice(1));
    if (fromHash && markers.has(fromHash)) selectPark(fromHash);
  }

  $("search").addEventListener("input", renderList);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && activeId) selectPark(null); });
  window.addEventListener("hashchange", () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (id !== (activeId || "")) selectPark(id || null);
  });

  load().catch((err) => {
    console.error(err);
    $("status").textContent = `Nepodařilo se načíst parky (${err.message}).`;
  });
})();
