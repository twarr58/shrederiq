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
  const icon = window.ShrederiqIcons.icon;
  const el = (tag, props, ...children) => {
    const n = Object.assign(document.createElement(tag), props || {});
    n.append(...children.flat());
    return n;
  };

  // --- mapa a podklad --------------------------------------------------------
  const map = L.map("map", { zoomControl: false }).setView(CZ_CENTER, 7);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  // Body parků ve vlastní vrstvě nad lajnami: canvas s lajnami leží v overlayPane
  // a jinak by zachytil ťuknutí i kliknutí na body parků pod sebou.
  map.createPane("parks").style.zIndex = 450;

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
      li.addEventListener("click", () => { selectPark(p.id); if (matchMedia("(max-width: 640px)").matches) setListOpen(false); });
      return li;
    });
    $("list").replaceChildren(...items);
    $("status").textContent = `${items.length} z ${parks.length} parků`;
    $("toggle-list").textContent = listOpen ? "Skrýt seznam" : `Seznam parků · ${parks.length}`;
  }

  // Seznam parků se zobrazí jen na požádání (tlačítko) nebo při hledání – jinak zabíral
  // celou výšku okna a překrýval mapu.
  let listOpen = false;
  function setListOpen(open) {
    listOpen = open;
    for (const id of ["legend", "list", "status"]) $(id).hidden = !open;
    $("toggle-list").setAttribute("aria-expanded", String(open));
    $("panel").classList.toggle("open", open);
    renderList();
  }
  $("toggle-list").addEventListener("click", () => setListOpen(!listOpen));

  // Karta: hlavička, tři čísla a sbalitelné sekce (Lajny otevřené, ostatní zavřené).
  // Jen Lajny se načtou hned (kreslí se do mapy), počasí až po rozbalení sekce –
  // karta tak nezabírá celou výšku okna a sekce jdou mít otevřené i vedle sebe.
  const SECTIONS = { trails: "Lajny", weather: "Počasí", nav: "Navigace", info: "Info" };
  let openSections = { trails: true };
  try { openSections = { ...openSections, ...JSON.parse(localStorage.getItem("shrederiq.sections") || "{}") }; } catch (_) { /* bez storage */ }

  function renderCard(p) {
    const close = el("button", { className: "card-btn", type: "button", title: "Zavřít park", ariaLabel: "Zavřít park" }, icon("close"));
    close.addEventListener("click", () => selectPark(null));
    // sbalit kartu na lištu s názvem: lajny zůstanou v mapě
    const fold = el("button", { className: "card-btn fold", type: "button" });
    fold.addEventListener("click", () => setCardMin(!cardMin));
    const tools = el("div", { className: "card-tools" }, fold, close);

    const where = P.describePark(p).find(([k]) => k === "Lokalita")[1];
    const head = el("div", { className: "card-head", title: "Sbalit / rozbalit kartu" },
      el("h2", {}, p.name),
      el("p", { className: "card-sub" }, el("i", { className: `dot ${p.kind}` }), `${P.KINDS[p.kind] || p.kind} · ${where}`));

    // chipy: stav trailů a teplota (po načtení počasí), lanovka, parkoviště, nadmořská výška (po načtení lajn)
    const chip = (text, cls = "") => el("span", { className: `chip-status ${cls}`.trim() }, text);
    const wxChip = chip("počasí…", "wx");
    const eleChip = chip("", "ele");
    eleChip.hidden = true;
    const closedChip = p.closed ? el("span", { className: "chip-status closed", title: p.closed }, "mimo provoz") : "";
    const chips = el("div", { className: "chips-row" }, closedChip, wxChip,
      p.lift ? chip(p.osm_lift ? `lanovka ${p.osm_lift}` : "lanovka / vlek") : "",
      p.parking ? chip(p.parking_name ? `P ${p.parking_name}` : "P u základny") : "",
      eleChip);

    const summary = el("p", { className: "card-summary muted" }, "Načítám lajny…");
    const bar = el("div", { className: "diff-bar-wrap" });

    const dest = N.destination(parks.find((x) => x.properties.id === p.id));
    const links = N.navLinks(dest, userPos);
    const actions = el("div", { className: "card-actions" },
      el("a", { className: "btn primary", href: links.mapy, target: "_blank", rel: "noopener" }, "Navigovat"),
      p.web ? el("a", { className: "btn", href: p.web, target: "_blank", rel: "noopener" }, "Web parku") : "");

    const trails = el("section", { className: "trails" }, el("p", { className: "muted" }, "Načítám lajny…"));
    const weather = el("section", { className: "weather" }, el("p", { className: "muted" }, "Načítám počasí…"));
    const bodies = { trails, weather, nav: renderNav(p.id), info: renderInfo(p) };
    const sections = Object.keys(SECTIONS).map((key) => {
      const d = el("details", { className: "section", open: !!openSections[key] });
      d.append(el("summary", {}, SECTIONS[key]), bodies[key]);
      d.addEventListener("toggle", () => {
        openSections[key] = d.open;
        try { localStorage.setItem("shrederiq.sections", JSON.stringify(openSections)); } catch (_) { /* bez storage */ }
      });
      return d;
    });

    head.addEventListener("click", (e) => { if (!e.target.closest("a, button")) setCardMin(!cardMin); });
    $("card").replaceChildren(tools, head, summary, chips, bar, actions, ...sections);
    $("card").hidden = false;
    setCardMin(cardMin);
    $("card").scrollTop = 0;
    loadTrails(p.id, trails, { summary, bar, eleChip });
    loadWeather(p.id, weather, wxChip); // jeden dotaz: chip hned, předpověď do sekce
  }

  // Sbalená karta = jen název a souhrn lajn. Na mobilu se karta otevírá sbalená,
  // aby byla vidět mapa s lajnami; ťuknutí do mapy ji zase sbalí.
  const isMobile = () => window.matchMedia("(max-width: 640px)").matches;
  let cardMin = isMobile();
  function setCardMin(min) {
    cardMin = min;
    const card = $("card");
    card.classList.toggle("min", min);
    const fold = card.querySelector(".fold");
    if (fold) {
      fold.replaceChildren(icon(min ? "chevron-up" : "chevron-down"));
      fold.title = min ? "Rozbalit kartu" : "Sbalit kartu (lajny zůstanou v mapě)";
      fold.setAttribute("aria-label", fold.title);
      fold.setAttribute("aria-expanded", String(!min));
    }
    if (min) card.scrollTop = 0;
  }
  map.on("click", () => { if (activeId && isMobile() && !cardMin) setCardMin(true); });

  function renderInfo(p) {
    const dl = el("dl");
    for (const [k, v] of P.describePark(p)) dl.append(el("dt", {}, k), el("dd", {}, v));
    if (p.web) dl.append(el("dt", {}, "Web"), el("dd", {}, el("a", { href: p.web, target: "_blank", rel: "noopener" }, new URL(p.web).hostname)));
    if (p.osm_base_source === "lift") dl.append(el("dt", {}, "Základna"), el("dd", {}, p.osm_lift ? `dolní stanice – ${p.osm_lift}` : "dolní stanice lanovky"));
    if (p.parking) dl.append(el("dt", {}, "Parkoviště"), el("dd", {}, p.parking_name || "ano", el("small", { className: "muted" }, " (P v mapě, navigace vede sem)")));
    const box = el("section", {}, dl);
    if (p.verified === false) box.append(el("p", { className: "note" }, "Údaje o parku (včetně polohy) jsou orientační a čekají na ověření."));
    return box;
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

  // oficiální seznamy lajn z webů parků (web/data/official.json generuje build): lajny,
  // které v OSM nejsou, se v kartě ukážou šedě s poznámkou „není v mapě“
  let officialData = null;
  function fetchOfficial() {
    officialData = officialData || fetch("data/official.json")
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null);
    return officialData;
  }

  function clearTrails() {
    if (trailLayer) map.removeLayer(trailLayer);
    trailLayer = null;
  }

  const DIFF_ORDER = ["green", "blue", "red", "black", "unknown"];

  async function loadTrails(id, box, head) {
    const [fc, official] = await Promise.all([fetchTrails(), fetchOfficial()]);
    const s = T.summarize(fc, id);
    const offPark = official && official[id];
    s.missing = offPark ? offPark.trails.filter((t) => !t.mapped) : [];
    s.officialSource = offPark ? offPark.source : null;
    if (activeId !== id || !box.isConnected) return;
    clearTrails();
    if (!s.segments) {
      head.summary.textContent = "Lajny tohoto parku zatím v mapě nejsou.";
      box.replaceChildren(
        s.missing.length ? missingTable(s.missing) : "",
        el("p", { className: "note" }, "Lajny tohoto parku zatím v mapě nejsou (OpenStreetMap je pro něj nemá).",
          ...(s.officialSource ? [" Seznam lajn: ", sourceLink(s.officialSource), "."] : [])));
      return;
    }
    head.summary.className = "card-summary";
    head.summary.replaceChildren(el("b", {}, N.formatDistance(s.lengthM)),
      el("span", { className: "muted" }, ` ve ${plural(s.namedCount, "lajně", "lajnách", "lajnách")}`
        + (s.longest ? ` · nejdelší ${s.longest.name || "bez názvu"} ${N.formatDistance(s.longest.lengthM)}` : "")
        + (s.missing.length ? ` · další ${s.missing.length} jen na webu parku` : "")));
    // pruh podílu obtížností s délkami pod ním
    const diffs = DIFF_ORDER.filter((d) => s.byDifficulty[d]);
    const info = (d) => T.difficultyInfo(d === "unknown" ? null : d);
    head.bar.replaceChildren(
      el("div", { className: "diff-bar", role: "img", ariaLabel: "Podíl obtížností: " + diffs.map((d) => `${info(d).label} ${N.formatDistance(s.byDifficulty[d])}`).join(", ") },
        ...diffs.map((d) => el("span", { style: `flex:${s.byDifficulty[d]};background:${info(d).color}` }))),
      el("div", { className: "diff-labels" }, ...diffs.map((d) => el("span", {}, `${info(d).label} ${N.formatDistance(s.byDifficulty[d])}`))));
    if (s.eleMin != null) {
      head.eleChip.textContent = `${s.eleMin}–${s.eleMax} m n. m.`;
      head.eleChip.hidden = false;
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
    // Lajny mají bílý obrys: podklad Mapy.com outdoor kreslí tytéž trasy tečkovaně,
    // bez obrysu se čáry slévají. Canvas kreslí vrstvy v pořadí jejich id (L.stamp),
    // proto se nejdřív vytvoří všechny obrysy a teprve potom lajny.
    const parts = []; // [latlngs nebo GeoJSON, styl, tooltip]
    for (const f of s.features) {
      if (f.properties.access) { // lesní / přístupová cesta – tence a čárkovaně, není to lajna
        parts.push([f, { color: "#8a8f98", weight: 2, opacity: 0.8, dashArray: "4 4" }, `lesní / přístupová cesta · ${N.formatDistance(f.properties.length_m)}`, false]);
        continue;
      }
      const runs = mode === "slope" ? T.slopeRuns(f) : null;
      const tooltip = trailTooltip(f.properties);
      if (runs) {
        for (const r of runs) {
          parts.push([r.coordinates.map(([lon, lat]) => [lat, lon]), { color: r.color, weight: 5, opacity: 0.95 }, `${tooltip} · tady ${r.slopeMax} %`, true]);
        }
      } else {
        const weight = f.properties.difficulty ? 4 : 3; // bez obtížnosti (šlapací okruhy) tenčí
        // spojka po neznačené cestě (doplněná mezi kusy lajny) čárkovaně
        const dash = f.properties.connector ? { dashArray: "6 6" } : {};
        parts.push([f, { color: T.difficultyInfo(f.properties.difficulty).color, weight, opacity: 0.95, ...dash },
          f.properties.connector ? `${tooltip} · neznačený úsek (lesní cesta / pěšina)` : tooltip, true]);
      }
    }
    const make = (geom, style, interactive) => (Array.isArray(geom)
      ? L.polyline(geom, { ...style, interactive, renderer: trailRenderer })
      : L.geoJSON(geom, { style, interactive, renderer: trailRenderer }));
    for (const [geom, style, , cased] of parts) {
      if (cased) make(geom, { color: "#fff", weight: style.weight + 3, opacity: 0.85 }, false).addTo(trailLayer);
    }
    for (const [geom, style, tooltip] of parts) make(geom, style, true).bindTooltip(tooltip, { sticky: true }).addTo(trailLayer);
    trailLayer.addTo(map);
  }

  const sourceLink = (url) => el("a", { href: url, target: "_blank", rel: "noopener" }, "web parku");

  function missingRow(t) {
    const d = T.difficultyInfo(t.difficulty);
    return el("tr", { className: "missing", title: "Lajna je na webu parku, ale v mapových datech (OpenStreetMap) zatím není." },
      el("td", {}, t.name, el("small", { className: "muted" }, " · není v mapě")),
      el("td", { className: `diff-${t.difficulty || "unknown"}` }, el("i", { className: "swatch", style: `background:${d.color}` }), d.label),
      el("td", { className: "num" }, t.length_m ? N.formatDistance(t.length_m) : "–"));
  }

  function missingTable(missing) {
    return el("table", { className: "trail-table" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Lajna"), el("th", { scope: "col" }, "Obtížnost"), el("th", { scope: "col", className: "num" }, "Délka"))),
      el("tbody", {}, ...missing.map(missingRow)));
  }

  function renderTrails(s, box, fit) {
    const mode = s.hasSlopes ? trailColor : "difficulty";
    drawTrails(s, mode);
    if (fit) {
      // na mobilu panel nahoře a karta dole překrývají mapu – lajny se vejdou mezi ně
      // na počítači je panel vlevo a karta vpravo – lajny se vejdou mezi ně
      const card = $("card").hidden ? null : $("card").getBoundingClientRect();
      const top = isMobile() ? $("panel").getBoundingClientRect().bottom + 10 : 30;
      const bottom = isMobile() && card ? window.innerHeight - card.top + 10 : 30;
      const left = isMobile() ? 20 : $("panel").getBoundingClientRect().right + 20;
      const right = !isMobile() && card ? window.innerWidth - card.left + 20 : 20;
      map.fitBounds(trailLayer.getBounds(), { paddingTopLeft: [left, top], paddingBottomRight: [right, bottom], maxZoom: 15 });
    }

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

    const name = (r) => r.name ? r.name : el("span", { className: "muted" }, r.segments > 1 ? `bez názvu (${plural(r.segments, "úsek", "úseky", "úseků")})` : "bez názvu");
    const table = el("table", { className: "trail-table" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Lajna"), el("th", { scope: "col" }, "Obtížnost"), el("th", { scope: "col", className: "num" }, "Délka"))),
      el("tbody", {}, ...s.rows.map((r) => {
        const d = T.difficultyInfo(r.difficulty);
        return el("tr", { className: r.difficulty ? "" : "muted" },
          el("td", {}, name(r), r.slopeMax != null ? el("small", { className: "muted trail-ele", title: "převýšení a největší sklon" }, icon("mountain"), ` ${r.eleMax - r.eleMin} m · max ${r.slopeMax} %`) : ""),
          el("td", { className: `diff-${r.difficulty || "unknown"}` }, el("i", { className: "swatch", style: `background:${d.color}` }), d.label),
          el("td", { className: "num" }, N.formatDistance(r.lengthM)));
      }), ...(s.missing || []).map(missingRow)),
      s.accessLengthM ? el("tfoot", {}, el("tr", { className: "muted" }, el("td", { colSpan: 2 }, "lesní a přístupové cesty"), el("td", { className: "num" }, N.formatDistance(s.accessLengthM)))) : "");

    box.replaceChildren(
      toggle ? el("div", { className: "wx-head" }, el("span", { className: "muted" }, "Barva v mapě"), toggle) : "",
      legend,
      table,
      el("p", { className: "attr" },
        s.sources.osm ? ["Lajny: © přispěvatelé ",
          el("a", { href: "https://www.openstreetmap.org/copyright", target: "_blank", rel: "noopener" }, "OpenStreetMap"),
          " (ODbL). ", s.official ? "Barvy podle značení parku (oficiální seznam), zbytek podle mtb:scale." : "Obtížnost podle mtb:scale, může se lišit od značení v parku."] : "",
        s.sources.trailforks ? [" Lajny z GPX: © ", el("a", { href: "https://www.trailforks.com/", target: "_blank", rel: "noopener" }, "Trailforks"),
          ` (${plural(s.sources.trailforks, "úsek", "úseky", "úseků")}, jen pro nekomerční použití).`] : "",
        s.sources.gpx ? ` ${plural(s.sources.gpx, "úsek", "úseky", "úseků")} z ručně dodaných GPX.` : "",
        s.snapped ? ` Průběh ${plural(s.snapped, "úseku", "úseků", "úseků")} podle sítě Mapy.com (routing).` : "",
        s.hasSlopes ? " Výšky: Mapy.com, sklon je orientační (výškový model, okno ~75 m)." : "",
        s.missing && s.missing.length ? [` ${s.missing.length === 1 ? "1 lajna je jen na webu parku, v mapě zatím není"
          : `${plural(s.missing.length, "lajna", "lajny", "lajn")} ${s.missing.length <= 4 ? "jsou" : "je"} jen na webu parku, v mapě zatím ${s.missing.length <= 4 ? "nejsou" : "není"}`}`,
          ...(s.officialSource ? [" (", sourceLink(s.officialSource), ")"] : []), "."] : ""));
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
          userMarker = L.circleMarker([userPos.lat, userPos.lon], { radius: 7, weight: 3, color: "#fff", fillColor: "#3a86ff", fillOpacity: 1, pane: "parks" })
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

  async function loadWeather(id, box, chip) {
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
      if (activeId === id && box.isConnected) {
        renderWeather(box, hit.data);
        if (chip) {
          const w = W.summarize(hit.data, new Date(), weatherStep);
          chip.textContent = `${w.trail.label} · ${w.current.temperature} °C ${w.current.label}`;
          chip.className = `chip-status wx ${w.trail.level}`;
        }
      }
    } catch (err) {
      console.warn("weather", err);
      if (box.isConnected) box.replaceChildren(el("p", { className: "muted" }, "Počasí se teď nepodařilo načíst."));
      if (chip) chip.hidden = true;
    }
  }

  function renderWeather(box, data) {
    const s = W.summarize(data, new Date(), weatherStep);
    const c = s.current;

    const now = el("div", { className: "wx-now" },
      el("span", { className: "wx-icon", title: c.label }, icon(c.icon, c.label)),
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
        el("span", { className: "wx-icon" }, icon(h.icon, h.label)),
        el("b", {}, `${Math.round(h.temperature)}°`),
        el("small", { className: h.precipitation > 0 ? "rain" : "muted" }, h.precipitation > 0 ? `${num(h.precipitation)} mm` : `${h.precipitationProbability} %`))));

    const daily = el("div", { className: "wx-days" }, ...s.daily.map((d) =>
      el("div", { className: "wx-day", title: `${d.label}, vítr až ${d.windMax} km/h` },
        el("b", {}, d.weekday),
        el("span", { className: "wx-icon" }, icon(d.icon, d.label)),
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
      const m = L.circleMarker([lat, lon], { radius: 7, weight: 2, color: "#fff", fillColor: color(p.kind), fillOpacity: p.closed ? 0.35 : 0.95, pane: "parks", bubblingMouseEvents: false })
        .bindTooltip(p.closed ? `${p.name} (mimo provoz)` : p.name, { direction: "top", offset: [0, -6] })
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

  $("search").addEventListener("input", () => { if (!listOpen && $("search").value.trim()) setListOpen(true); else renderList(); });
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
