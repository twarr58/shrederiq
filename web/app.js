(function () {
  "use strict";

  const cfg = window.SHREDERIQ_CONFIG || {};
  const P = window.ShrederiqParks;
  const CZ_CENTER = [49.8, 15.5];
  const PARK_ZOOM = 14;

  const $ = (id) => document.getElementById(id);
  const el = (tag, props, ...children) => {
    const n = Object.assign(document.createElement(tag), props || {});
    n.append(...children);
    return n;
  };

  // --- mapa a podklad --------------------------------------------------------
  const map = L.map("map", { zoomControl: false }).setView(CZ_CENTER, 7);
  L.control.zoom({ position: "bottomright" }).addTo(map);

  if (cfg.mapyApiKey) {
    // Mapy.com – podmínky vyžadují atribuci i logo (https://developer.mapy.com/)
    const mapset = cfg.mapyMapset || "outdoor";
    L.tileLayer(`https://api.mapy.com/v1/maptiles/${mapset}/256/{z}/{x}/{y}?apikey=${encodeURIComponent(cfg.mapyApiKey)}`, {
      minZoom: 0,
      maxZoom: 19,
      attribution: '<a href="https://api.mapy.com/copyright" target="_blank" rel="noopener">&copy; Seznam.cz a.s. a další</a>',
    }).addTo(map);
    const Logo = L.Control.extend({
      options: { position: "bottomleft" },
      onAdd: () => el("a", { className: "mapy-logo", href: "https://mapy.com/", target: "_blank", rel: "noopener" },
        el("img", { src: "https://api.mapy.com/img/api/logo.svg", alt: "Mapy.com" })),
    });
    new Logo().addTo(map);
  } else {
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
    }).addTo(map);
  }

  // --- stav ------------------------------------------------------------------
  let parks = [];           // GeoJSON features
  const markers = new Map(); // id → L.circleMarker
  let activeId = null;

  const color = (kind) => getComputedStyle(document.documentElement).getPropertyValue(`--${kind}`).trim() || "#e4572e";

  function renderList() {
    const items = P.filterParks(parks, $("search").value).map((f) => {
      const p = f.properties;
      const li = el("li", { className: p.id === activeId ? "active" : "" },
        el("i", { className: `dot ${p.kind}` }), " ", p.name, el("small", {}, [p.town, p.region].filter(Boolean).join(", ")));
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

    $("card").replaceChildren(close, el("h2", {}, p.name), dl, ...notes);
    $("card").hidden = false;
  }

  function selectPark(id, { fly = true } = {}) {
    const f = parks.find((x) => x.properties.id === id);
    if (activeId && markers.has(activeId)) markers.get(activeId).setStyle({ weight: 2, radius: 7 });
    activeId = f ? id : null;

    if (f) {
      markers.get(id).setStyle({ weight: 4, radius: 10 }).bringToFront();
      const [lon, lat] = f.geometry.coordinates;
      if (fly) map.flyTo([lat, lon], PARK_ZOOM, { duration: 0.8 });
      renderCard(f.properties);
      history.replaceState(null, "", `#${id}`);
    } else {
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
