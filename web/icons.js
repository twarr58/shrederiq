// Ikony aplikace jako inline SVG (žádná emoji – ta se na každém systému kreslí jinak).
// Obrys 24×24, barva podle currentColor, velikost 1em (řídí se font-size rodiče).
(function (root) {
  "use strict";

  const CLOUD = "M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.2 10.6 3.75 3.75 0 0 0 7 18z";
  const CLOUD_HIGH = "M7 15h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.2 7.6 3.75 3.75 0 0 0 7 15z";
  const PATHS = {
    sun: ["M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z",
      "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"],
    moon: ["M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10z"],
    "cloud-sun": ["M8 3v1.5M3 8h1.5M4.5 4.5l1 1M11.5 4.5l-1 1", "M5.6 10.4A3 3 0 1 1 10.6 7", CLOUD],
    cloud: [CLOUD],
    fog: ["M4 9h16M6 13h12M4 17h16"],
    drizzle: [CLOUD_HIGH, "M8 18.5v1M12 18.5v1M16 18.5v1"],
    rain: [CLOUD_HIGH, "M8 18l-1 3M12 18l-1 3M16 18l-1 3"],
    snow: [CLOUD_HIGH, "M8 19h.01M12 19h.01M16 19h.01M10 21.5h.01M14 21.5h.01"],
    storm: [CLOUD_HIGH, "M12.5 15.5l-2 3.5h3l-2 3.5"],
    unknown: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7M12 16.8h.01"],
    locate: ["M12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12z", "M12 2v3M12 19v3M2 12h3M19 12h3", "M12 11a1 1 0 1 0 0 2 1 1 0 0 0 0-2z"],
    mountain: ["M2.5 19.5l6.5-12 4.5 8 2.5-4 5.5 8z"],
    close: ["M6 6l12 12M18 6L6 18"],
    "chevron-down": ["M6 9l6 6 6-6"],
    "chevron-up": ["M6 15l6-6 6 6"],
  };

  const NS = "http://www.w3.org/2000/svg";

  // icon("sun", "Jasno") → <svg class="icon icon-sun" role="img" aria-label="Jasno">…</svg>
  function icon(name, label) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", `icon icon-${name}`);
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    if (label) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label); }
    else svg.setAttribute("aria-hidden", "true");
    for (const d of PATHS[name] || PATHS.unknown) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  }

  const api = { icon, names: Object.keys(PATHS) };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShrederiqIcons = api;
})(typeof window !== "undefined" ? window : globalThis);
