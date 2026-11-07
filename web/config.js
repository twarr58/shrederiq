// Runtime konfigurace. Při deployi na GitHub Pages ji workflow přepíše
// (viz .github/workflows/pages.yml).
//
// mapyApiKey – klíč k Mapy.com API (https://developer.mapy.com/) vložený při buildu
//              z GitHub secretu MAPY_API_KEY. Prázdný = podklad z OpenStreetMap.
//              POZOR: klíč vložený sem je veřejně čitelný pro každého návštěvníka.
window.SHREDERIQ_CONFIG = {
  mapyApiKey: "",
  mapyMapset: "outdoor",
};
