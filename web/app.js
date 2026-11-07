(function () {
  "use strict";

  const cfg = window.SHREDERIQ_CONFIG || {};
  const P = window.ShrederiqParks;
  const W = window.ShrederiqWeather;
  const N = window.ShrederiqNav;
  const T = window.ShrederiqTrails;
  const CZ_CENTER = [49.8, 15.5];
  const PARK_ZOOM = 14;

  const $ = (id) => document.getElementById(id);
  const num = (x) => String(x).replace(".", ","); // české desetinné číslo
  const plural = (n, one, few, many) => `${n} ${n === 1 ? one : n >= 2 && n <= 4 ? few : many}`;
  const el = (tag, props, ...children) => {
    const n = Object.assign(document.createElement(tag), props || {});
    n.append(...children.flat());
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
  let trailCounts = null;    // id → počet pojmenovaných lajn (po načtení trails.geojson)
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
      const parts = [dist.has(f) ? `${N.formatDistance(dist.get(f))} vzdušně` : "", where];
      if (trailCounts) {
        const n = trailCounts[p.id] || 0;
        parts.push(n ? plural(n, "lajna", "lajny", "lajn") : "bez lajn v datech");
      }
      const li = el("li", { className: p.id === activeId ? "active" : "" },
        el("i", { className: `dot ${p.kind}` }), " ", p.name,
        el("small", {}, parts.filter(Boolean).join(" · ")));
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
    if (p.osm_base_source === "lift") dl.append(el("dt", {}, "Základna"), el("dd", {}, p.osm_lift ? `dolní stanice – ${p.osm_lift}` : "dolní stanice lanovky"));
    if (p.parking) dl.append(el("dt", {}, "Parkoviště"), el("dd", {}, p.parking_name || "ano", el("small", { className: "muted" }, " (P v mapě, navigace vede sem)")));

    const close = el("button", { className: "close", type: "button", title: "Zavřít", ariaLabel: "Zavřít" }, "×");
    close.addEventListener("click", () => selectPark(null));

    const notes = [];
    if (p.verified === false) notes.push(el("p", { className: "note" }, "Údaje o parku (včetně polohy) jsou orientační a čekají na ověření."));

    const weather = el("section", { className: "weather" }, el("p", { className: "muted" }, "Načítám počasí…"));
    const trails = el("section", { className: "trails" }, el("p", { className: "muted" }, "Načítám lajny…"));
    $("card").replaceChildren(close, el("h2", {}, p.name), dl, renderNav(p.id), trails, weather, ...notes);
    $("card").hidden = false;
    $("card").scrollTop = 0;
    loadTrails(p.id, trails);
    loadWeather(p.id, weather);
  }

  // --- lajny (OSM, web/data/trails.geojson generuje workflow pages) ------------
  let trailsData = null; // Promise<FeatureCollection | null>
  let trailLayer = null;

  function fetchTrails() {
    trailsData = trailsData || fetch("data/trails.geojson")
      .then((res) => (res.ok ? res.json() : null))
      .catch((err) => { console.warn("trails", err); return null; });
    return trailsData;
  }

  function clearTrails() {
    if (trailLayer) map.removeLayer(trailLayer);
    trailLayer = null;
  }

  async function loadTrails(id, box) {
    const s = T.summarize(await fetchTrails(), id);
    if (activeId !== id || !box.isConnected) return;
    clearTrails();
    if (!s.segments) {
      box.replaceChildren(el("p", { className: "note" },
        "Lajny tohoto parku zatím v datech nejsou (OpenStreetMap je pro něj nemá). Doplníme z Trailforks."));
      return;
    }
    renderTrails(s, box, true);
  }

  let trailColor = "difficulty"; // nebo "slope"
  try { trailColor = localStorage.getItem("shrederiq.trailColor") === "slope" ? "slope" : "difficulty"; } catch (_) { /* bez storage */ }

  function trailTooltip(p) {
    const parts = [p.name || "bez názvu", T.difficultyInfo(p.difficulty).label, N.formatDistance(p.length_m)];
    if (p.source === "trailforks") parts.push("GPX Trailforks");
    else if (p.source === "gpx") parts.push("GPX");
    if (p.slope_max != null) parts.push(`max ${p.slope_max} %`, `${p.ele_min}–${p.ele_max} m n. m.`);
    return parts.join(" · ");
  }

  const trailRenderer = L.canvas({ padding: 0.3 }); // stovky úseků sklonu – canvas je rychlejší než SVG

  function drawTrails(s, mode) {
    clearTrails();
    trailLayer = L.featureGroup();
    for (const f of s.features) {
      if (f.properties.access) { // lesní / přístupová cesta – tence a čárkovaně, není to lajna
        L.geoJSON(f, { style: { color: "#8a8f98", weight: 2, opacity: 0.8, dashArray: "4 4" }, renderer: trailRenderer })
          .bindTooltip(`lesní / přístupová cesta · ${N.formatDistance(f.properties.length_m)}`, { sticky: true }).addTo(trailLayer);
        continue;
      }
      const runs = mode === "slope" ? T.slopeRuns(f) : null;
      const tooltip = trailTooltip(f.properties);
      if (runs) {
        for (const r of runs) {
          L.polyline(r.coordinates.map(([lon, lat]) => [lat, lon]), { color: r.color, weight: 5, opacity: 0.95, renderer: trailRenderer })
            .bindTooltip(`${tooltip} · tady ${r.slopeMax} %`, { sticky: true }).addTo(trailLayer);
        }
      } else {
        L.geoJSON(f, { style: { color: T.difficultyInfo(f.properties.difficulty).color, weight: 4, opacity: 0.9 }, renderer: trailRenderer })
          .bindTooltip(tooltip, { sticky: true }).addTo(trailLayer);
      }
    }
    trailLayer.addTo(map);
  }

  function renderTrails(s, box, fit) {
    const mode = s.hasSlopes ? trailColor : "difficulty";
    drawTrails(s, mode);
    if (fit) map.fitBounds(trailLayer.getBounds(), { padding: [30, 30], maxZoom: 15 });

    const toggle = s.hasSlopes ? el("div", { className: "wx-toggle", role: "group", ariaLabel: "Barva lajn" },
      ...[["difficulty", "obtížnost"], ["slope", "sklon"]].map(([m, label]) => {
        const b = el("button", { type: "button", className: m === mode ? "on" : "" }, label);
        b.addEventListener("click", () => {
          trailColor = m;
          try { localStorage.setItem("shrederiq.trailColor", m); } catch (_) { /* bez storage */ }
          renderTrails(s, box, false);
        });
        return b;
      })) : "";

    const legend = mode === "slope"
      ? el("div", { className: "slope-legend" }, ...T.SLOPE_BINS.map((b) =>
        el("span", {}, el("i", { className: "swatch", style: `background:${b.color}` }), b.label)))
      : "";

    // statistiky parku: celková délka, počet lajn, nejdelší lajna, délky podle obtížnosti, převýšení
    const stat = (label, value, sub) => el("div", { className: "stat" },
      el("b", {}, value), el("span", {}, label), sub ? el("small", { className: "muted" }, sub) : "");
    const byDiff = ["green", "blue", "red", "black", "unknown"].filter((d) => s.byDifficulty[d])
      .map((d) => el("span", { className: "diff" },
        el("i", { className: "swatch", style: `background:${T.difficultyInfo(d === "unknown" ? null : d).color}` }),
        `${T.difficultyInfo(d === "unknown" ? null : d).label} ${N.formatDistance(s.byDifficulty[d])}`));
    const stats = el("div", { className: "trail-stats" },
      stat("celkem", N.formatDistance(s.lengthM), s.accessLengthM ? `+ ${N.formatDistance(s.accessLengthM)} přístupových cest` : ""),
      stat(plural(s.namedCount, "lajna", "lajny", "lajn").replace(/^\d+ /, ""), String(s.namedCount),
        s.segments !== s.namedCount ? `${plural(s.segments, "úsek", "úseky", "úseků")} v OSM` : ""),
      s.longest ? stat("nejdelší", N.formatDistance(s.longest.lengthM), s.longest.name || "bez názvu") : "",
      s.eleMin != null ? stat("převýšení", `${s.eleMax - s.eleMin} m`, `${s.eleMin}–${s.eleMax} m n. m.`) : "",
      byDiff.length ? el("div", { className: "stat diffs" }, ...byDiff) : "");

    const list = el("ul", { className: "trail-list" }, ...s.rows.map((r) =>
      el("li", {},
        el("i", { className: "swatch", style: `background:${T.difficultyInfo(r.difficulty).color}`, title: T.difficultyInfo(r.difficulty).label }),
        el("span", { className: "name" },
          r.name ? r.name : el("span", { className: "muted" }, r.segments > 1 ? `bez názvu (${plural(r.segments, "úsek", "úseky", "úseků")})` : "bez názvu"),
          r.slopeMax != null ? el("small", { className: "muted" }, ` ↕ ${r.eleMax - r.eleMin} m · max ${r.slopeMax} %`) : ""),
        el("span", { className: "len" }, N.formatDistance(r.lengthM)))));

    box.replaceChildren(
      el("div", { className: "wx-head" },
        el("h3", {}, "Lajny"),
        toggle),
      stats,
      legend,
      list,
      el("p", { className: "attr" },
        s.sources.osm ? ["Lajny: © přispěvatelé ",
          el("a", { href: "https://www.openstreetmap.org/copyright", target: "_blank", rel: "noopener" }, "OpenStreetMap"),
          " (ODbL). Obtížnost podle mtb:scale, může se lišit od značení v parku."] : "",
        s.sources.trailforks ? [" Lajny z GPX: © ", el("a", { href: "https://www.trailforks.com/", target: "_blank", rel: "noopener" }, "Trailforks"),
          ` (${plural(s.sources.trailforks, "úsek", "úseky", "úseků")}, jen pro nekomerční použití).`] : "",
        s.sources.gpx ? ` ${plural(s.sources.gpx, "úsek", "úseky", "úseků")} z ručně dodaných GPX.` : "",
        s.snapped ? ` Průběh ${plural(s.snapped, "úseku", "úseků", "úseků")} podle sítě Mapy.com (routing).` : "",
        s.hasSlopes ? " Výšky: Mapy.com, sklon je orientační (výškový model, okno ~75 m)." : ""));
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

  let parkingMarker = null;
  const parkingIcon = L.divIcon({ className: "parking-icon", html: "P", iconSize: [20, 20] });

  function selectPark(id, { fly = true } = {}) {
    const f = parks.find((x) => x.properties.id === id);
    if (activeId && markers.has(activeId)) markers.get(activeId).setStyle({ weight: 2, radius: 7 });
    activeId = f ? id : null;

    clearTrails();
    if (parkingMarker) { map.removeLayer(parkingMarker); parkingMarker = null; }
    if (f) {
      markers.get(id).setStyle({ weight: 4, radius: 10 }).bringToFront();
      const pk = f.properties.parking;
      if (pk) {
        parkingMarker = L.marker([pk[1], pk[0]], { icon: parkingIcon, keyboard: false })
          .bindTooltip(f.properties.parking_name || "Parkoviště", { direction: "top" }).addTo(map);
      }
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

  // --- pumptracky (OSM, web/data/pumptracks.geojson generuje build) -----------
  const pumpLayer = L.layerGroup();
  let pumpLoaded = false;
  async function loadPumptracks() {
    if (pumpLoaded) return;
    pumpLoaded = true;
    try {
      const res = await fetch("data/pumptracks.geojson");
      if (!res.ok) return;
      const fc = await res.json();
      for (const f of fc.features) {
        const [lon, lat] = f.geometry.coordinates;
        const p = f.properties;
        const name = p.name || "Pumptrack";
        const links = N.navLinks([lon, lat], userPos);
        L.circleMarker([lat, lon], { radius: 4, weight: 1, color: "#fff", fillColor: color("pumptrack"), fillOpacity: 0.8, renderer: trailRenderer })
          .bindTooltip(name, { direction: "top", offset: [0, -4] })
          .bindPopup(`<b>${name.replace(/</g, "&lt;")}</b><br>${p.surface ? `povrch: ${p.surface}<br>` : ""}<a href="${links.mapy}" target="_blank" rel="noopener">Navigovat</a> · <a href="https://www.openstreetmap.org/${p.osm}" target="_blank" rel="noopener">OSM</a>`)
          .addTo(pumpLayer);
      }
      $("layer-pumptracks").title = `${fc.features.length} pumptracků z OpenStreetMap`;
    } catch (err) {
      console.warn("pumptracks", err);
    }
  }
  function updatePumpLayer() {
    const on = $("layer-pumptracks").checked;
    try { localStorage.setItem("shrederiq.pumptracks", on ? "1" : "0"); } catch (_) { /* bez storage */ }
    if (on) { loadPumptracks(); pumpLayer.addTo(map); } else { map.removeLayer(pumpLayer); }
  }
  try { if (localStorage.getItem("shrederiq.pumptracks") === "0") $("layer-pumptracks").checked = false; } catch (_) { /* bez storage */ }
  $("layer-pumptracks").addEventListener("change", updatePumpLayer);

  // --- data ------------------------------------------------------------------
  async function load() {
    const res = await fetch("data/parks.geojson", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const fc = await res.json();
    const errors = P.validateParks(fc);
    if (errors.length) console.warn("parks.geojson:", errors);
    // základna a parkoviště z OSM (generuje build, lokálně nemusí existovat)
    const osmInfo = await fetch("data/parks_osm.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
    parks = P.applyOsm(fc.features, osmInfo);

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
    updatePumpLayer();
    fetchTrails().then((fc) => {
      if (!fc) return;
      trailCounts = Object.fromEntries(parks.map((f) => [f.properties.id, T.summarize(fc, f.properties.id).namedCount]));
      renderList();
    });
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
