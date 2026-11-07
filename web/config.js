// Runtime konfigurace. Při deployi na GitHub Pages ji workflow přepíše
// (viz .github/workflows/pages.yml).
//
// mapyApiKey – klíč k Mapy.com API (https://developer.mapy.com/) vložený při buildu
//              z GitHub secretu MAPY_API_KEY. Prázdný = podklad z OpenStreetMap.
//              POZOR: klíč vložený sem je veřejně čitelný pro každého návštěvníka.
// mapyMinZoom – od jakého zoomu použít Mapy.com (default 11); přehled ČR jde z OSM,
//              aby se šetřil limit 100k dlaždic měsíčně.
window.SHREDERIQ_CONFIG = {
  mapyApiKey: "",
  mapyMapset: "outdoor",
};
