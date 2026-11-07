# shrederiq

Interaktivní mapa českých bikeparků a trail center. Po rozkliknutí parku se zobrazí
všechny jeho trailové lajny a základní info (web, otevírací doba, lanovka, ceník).
Bonus: lajny obarvené podle sklonu.

> Stav: návrh / rozpracováno. Tohle README popisuje cílovou podobu projektu.

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

Navržený tvar dat:

```text
data/
  parks.geojson    # body parků + metadata
  trails.geojson   # lajny trailů (park_id, name, difficulty, length_m, slope segmenty)
```

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
