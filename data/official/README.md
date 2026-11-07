# Oficiální seznamy trailů

Ručně opsané z webů provozovatelů (#22, krok 3c): `scripts/official_check.py` je porovná
s lajnami, které máme z OSM, a v souhrnu workflow `osm` ukáže, co chybí, co se jmenuje
jinak a kde se liší délka nebo obtížnost.

```json
{
  "source": "https://…",          // odkud opsáno
  "checked": "2026-11-07",        // kdy
  "trails": [
    {"name": "Fun Flow", "difficulty": "red", "length_m": 1700}
  ]
}
```

- Soubor `<id parku z web/data/parks.geojson>.json`.
- `difficulty`: `green` / `blue` / `red` / `black`, nebo vynechat.
- `length_m`: délka podle provozovatele v metrech, nebo vynechat.
- Jména se porovnávají bez diakritiky a velikosti písmen; stačí, když jedno jméno obsahuje
  druhé (`Flowčko` ~ `Modrý Flowčko`).
