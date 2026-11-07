# shrederiq

Interaktivní mapa českých bikeparků a trail center. Po rozkliknutí parku se zobrazí
všechny jeho trailové lajny a základní info (web, otevírací doba, lanovka, ceník).
Bonus: lajny obarvené podle sklonu.

> Stav: **fáze 1** – mapa parků s info panelem. Trasy jednotlivých lajn zatím
> nejsou (chybí data), přijdou ve fázi 2. Seznam parků je orientační a čeká na ověření.

## Funkce

- mapa ČR s body bikeparků / trail center,
- klik na park → přiblížení, zobrazení jeho trailů a info panel,
- trailové lajny obarvené podle sklonu (případně podle obtížnosti),
- metadata parků: web, otevřeno/zavřeno, počet trailů, lanovka, ceník.

## Zdroje dat

| Priorita | Zdroj | Na co | Poznámka |
|---|---|---|---|
| 1 | [Trailforks](https://www.trailforks.com/) | geometrie trailů (GPX / API) | nejlepší pokrytí českých parků; API vyžaduje registraci |
| 2 | [OpenStreetMap](https://www.openstreetmap.org/) (Overpass API) | doplnění chybějících trailů | zdarma, otevřená licence, pokrytí jen částečné |
| 3 | oficiální weby parků | metadata, občas GPX | ručně nebo scrapováním |
| — | [Mapy.com](https://developer.mapy.com/) | mapový podklad (raster tiles) + Elevation API | geometrie trailů z něj získat nejdou |

## Datová pipeline

1. **Seznam parků** — z existujících seznamů (MTB Czech, České bikeparky) a vlastního
   researche: název, souřadnice, web, počet trailů.
2. **Geometrie trailů** — pro každý park GPX z Trailforks (ručně nebo přes API),
   chybějící doplnit z OSM, např. Overpass dotazem na `mtb:scale=*`, `leisure=bike_park`.
3. **Normalizace** — vše do jednotného GeoJSON (`LineString`) s atributy:
   název, obtížnost, délka, ID parku.
4. **Výšky a sklon** — vzorkování bodů podél trailu → Mapy.com Elevation API
   (až 256 bodů na request) → sklon v % mezi body, uložený jako atribut segmentů / barva.
5. **Metadata** — doplnění info o parcích (web, provoz, lanovka, ceník).

## Implementace

### Fáze 1 – mapa parků (hotovo)

Statický web bez buildu (stejný stack jako emhadecko), nasazený na GitHub Pages.

V aplikaci:

- mapa a seznam parků, hledání bez ohledu na diakritiku, odkaz `#id` na park,
- **počasí** v panelu parku z [Open-Meteo](https://open-meteo.com/) (zdarma, bez klíče):
  aktuální stav, 24 h po hodině nebo po 3 h, výhled na 3 dny,
  odhad stavu trailů podle srážek za 48 h,
- **navigace**: 📍 seřadí parky podle vzdálenosti. „Navigovat“ otevře Mapy.com
  nebo Google Maps. S klíčem Mapy.com umí vykreslit trasu autem přímo v mapě
  (routing API).

```text
web/
  index.html          # stránka
  app.js              # mapa (Leaflet), seznam, vyhledávání, info panel, #odkaz na park
  parks.js            # čisté funkce: validace dat, filtr, popis parku (testované)
  weather.js          # počasí z Open-Meteo: hodinová / 3h předpověď, 3 dny, stav trailů
  nav.js              # navigace: odkazy do Mapy.com / Google Maps, Mapy.com routing
  config.js           # runtime konfigurace (klíč Mapy.com), přepisuje ji pages workflow
  style.css
  data/parks.geojson  # seznam parků – zdroj pravdy, needitovat jinde
tests/*.test.js       # unit testy JS (parks.test.js validuje i web/data/parks.geojson)
tests/test_*.py       # unit testy Python skriptů
```

Formát `web/data/parks.geojson` – `FeatureCollection` bodů (`[lon, lat]`), vlastnosti:

| Pole | Typ | Popis |
|---|---|---|
| `id` | string, kebab-case | unikátní ID parku (použije se i jako `park_id` u trailů a v URL `#id`) |
| `name` | string | název |
| `kind` | `bikepark` \| `trail_center` | typ |
| `town`, `region` | string | obec, kraj |
| `web` | `https://…` \| null | oficiální web |
| `lift` | bool \| null | lanovka / vlek |
| `trails_count` | int \| null | počet trailů |
| `parking` | `[lon, lat]` \| null | cíl navigace (parkoviště / dolní stanice); bez něj se naviguje na bod parku |
| `verified` | bool | `false` = poloha a údaje jsou orientační |

Neznámé hodnoty jsou `null` a v UI se zobrazí jako „neověřeno“.

### Fáze 2 – trailové lajny (až budou data)

- `web/data/trails.geojson` – `LineString` s `park_id`, `name`, `difficulty`, `length_m`, `source`,
- skript(y) v `scripts/` pro import GPX z Trailforks a Overpass → normalizovaný GeoJSON,
- výpočet sklonu přes Mapy.com Elevation API (segmenty se sklonem v %),
- ve frontendu: po kliknutí na park načíst jeho lajny a obarvit je podle sklonu / obtížnosti.

### Fáze 3 – metadata

Otevírací doba, ceník, stav provozu – ručně nebo scrapováním webů parků.

## Lokální spuštění

```bash
python3 -m http.server -d web 8000
```

Pak otevři <http://localhost:8000>. Bez klíče Mapy.com se použije podklad OpenStreetMap.

```bash
node --test tests/*.test.js
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Kontrola seznamu parků proti OSM

`scripts/fetch_osm_parks.py` stáhne z Overpass API bikeparky v ČR (`leisure=bike_park`)
a spočítá MTB cesty (`mtb:scale`, `mtb:scale:dh`) v jejich okolí. Pak je porovná
s `web/data/parks.geojson`. Report ukáže:

- odchylky poloh našich parků,
- naše parky, které v OSM chybí,
- bikeparky z OSM, které nejsou v našem seznamu.

Skript jen navrhuje, `parks.geojson` neupravuje.

```bash
python3 scripts/fetch_osm_parks.py --report osm-report.md --geojson osm-bike-parks.geojson
```

Workflow `osm` ho pouští u PR, které mění data nebo skript, a každé pondělí.
Report najdeš v souhrnu běhu (Summary) a jako artifact `osm-report`.

## Nasazení

Workflow `pages` nasadí `web/` na GitHub Pages při pushi do `main`
(v Settings → Pages nastav Source na **GitHub Actions**). Pro podklad Mapy.com
přidej repo secret `MAPY_API_KEY`. Klíč je ve stránce veřejně čitelný, proto ho
v administraci Mapy.com omez na doménu GitHub Pages.

## Frontend

- statický web, [Leaflet](https://leafletjs.com/) nebo [MapLibre](https://maplibre.org/),
- podklad Mapy.com tiles,
- vrstva parků (body) + vrstva trailů (`LineString`),
- info panel parku a barevná škála sklonu / obtížnosti.

## Doporučený stack

- **Data:** Trailforks API / GPX + Overpass + Mapy.com Elevation
- **Formát:** GeoJSON
- **Mapa:** Leaflet nebo MapLibre + Mapy.com tiles
- **Hosting:** statický web + JSON (např. GitHub Pages), případně lehký backend

## Náročnost

| Část | Náročnost | Poznámka |
|---|---|---|
| Seznam parků | nízká | existující seznamy + ruční doplnění |
| Trailforks data | střední | API vyžaduje registraci; alternativně ruční GPX |
| OSM data | nízká | Overpass, ale neúplné |
| Elevation + sklon | nízká | Mapy.com Elevation API (až 256 bodů/request) |
| Frontend mapa | střední | standardní web mapa |
| Údržba | průběžná | Trailforks se mění, parky přibývají |

## Rizika a omezení

- Data z Trailforks mají licenční omezení (projekt musí zůstat zdarma / share-alike).
- OSM pokrývá bikeparky jen částečně.
- Z Mapy.com se lajny vytáhnout nedají — slouží jen jako podklad a zdroj výšek.
- Některé parky nemají oficiální GPX vůbec.

Největší práce je sběr a normalizace trailových GPX.

## Přispívání

Commity a názvy PR se řídí [Conventional Commits](https://www.conventionalcommits.org/)
a kontroluje je CI (`commitlint.config.mjs`). Šablonu commit zprávy zapneš takhle:

```bash
git config commit.template .gitmessage
```

Příklady: `feat(web): …`, `feat(data): …`, `fix(scripts): …`, `ci: …`, `docs: …`.
Verze GitHub Actions hlídá Dependabot (`.github/dependabot.yml`).
