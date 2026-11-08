# Oficiální seznamy trailů

Ručně opsané z webů provozovatelů (#22, krok 3c): `scripts/official_check.py` je porovná
s lajnami, které máme z OSM, a v souhrnu workflow `osm` ukáže, co chybí, co se jmenuje
jinak a kde se liší délka nebo obtížnost.

```json
{
  "source": "https://…",          // odkud opsáno
  "checked": "2026-11-07",        // kdy
  "trails": [
    {"name": "Fun Flow", "difficulty": "red", "length_m": 1700},
    {"name": "DHK", "aliases": ["iXS Downhill", "Downhill"]}
  ]
}
```

- Soubor `<id parku z web/data/parks.geojson>.json`.
- `difficulty`: `green` / `blue` / `red` / `black`, nebo vynechat.
- `length_m`: délka podle provozovatele v metrech, nebo vynechat.
- `aliases`: jména, pod kterými je trail v OSM, když se od oficiálního liší jinak než
  předponou parku nebo překlepem (Klínovec: DHK = „iXS Downhill“). Pište je přesně jako v OSM.
- Spárované úseky dostanou při buildu oficiální jméno a barvu (`--apply`), původní jméno
  zůstane v `osm_name`. Jeden trail může mít v OSM víc jmen (`Kozí trail` i `TT Kozí trail`).
- Jména se porovnávají bez diakritiky, bez obecných slov (trail, trať, lajna, line, horní /
  dolní část…), bez slov z názvu parku (`TT`, `Spičák`), se sjednocenými barvami
  (`Modrá trať` ~ `Modrý trail`, `Flowčko` ~ `#2 Modrý Flowčko`) a s tolerancí jednoho
  písmene v delších slovech (`Rýbrcour` ~ `Rýbrcoul`).
