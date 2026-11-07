# Ruční GPX lajn

Pro parky, které v OpenStreetMap lajny nemají (Monínec, Harrachov, Rokytnice, Kopřivná, Římov…).

```text
data/gpx/<id parku z web/data/parks.geojson>/
  red__Flow.gpx          # obtížnost z předpony: green__ blue__ red__ black__
  trailforks-export.gpx  # export z Trailforks: název z <name>, obtížnost z <type> / <desc>
```

- Jeden `<trk>` = jedna lajna. Soubor může mít víc tras.
- Název: `<trk><name>`, jinak název souboru bez předpony a přípony.
- Obtížnost: předpona souboru, jinak slovo green / blue / red / black v `<type>`, `<desc>` nebo `<cmt>`.
- Při buildu (`scripts/gpx_trails.py --merge web/data/trails.geojson`) se lajny připojí k lajnám
  z OSM, dostanou sklon a v panelu parku mají zdroj „GPX“ / „Trailforks“.
- GPX z Trailforks jsou jen pro nekomerční použití s atribucí; ta se zobrazí automaticky.
