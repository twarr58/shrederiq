#!/usr/bin/env bash
# Stáhne OSM výřez Česka (Geofabrik, aktualizovaný denně), vyfiltruje MTB cesty, lanovky,
# parkoviště a relace route=mtb a vytáhne data kolem parků do JSON ve tvaru odpovědi Overpass.
#   scripts/osm_extract.sh výstup.json [pumptracky.geojson] [cela-cr.json]
# MTB_PBF=cesta: vyfiltrovaný výřez; když existuje, přeskočí se stažení (cache po dnech v Actions).
# MTB_PBF_FALLBACK=cesta: starší vyfiltrovaný výřez (např. včerejší z cache); použije se, když
#   Geofabrik nestihne odpovědět v DOWNLOAD_BUDGET sekundách (výchozí 1080 = 18 min).
# Na GitHub runneru doinstaluje osmium-tool a pyosmium (apt).
set -euo pipefail
OUT=${1:?výstupní JSON}
PUMP=${2:-}
COUNTRY=${3:-}
TMP=${RUNNER_TEMP:-/tmp}
URL=${OSM_PBF_URL:-https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf}

if ! command -v osmium >/dev/null || ! python3 -c 'import osmium' 2>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq osmium-tool python3-pyosmium >/dev/null
fi

MTB_PBF=${MTB_PBF:-$TMP/mtb.osm.pbf}
FALLBACK=${MTB_PBF_FALLBACK:-}
BUDGET=${DOWNLOAD_BUDGET:-1080}
SRC=$MTB_PBF

# Stažení s pevným rozpočtem: Geofabrik občas posílá 0,5 MB/s a 950 MB pak nestihne
# ani limit kroku v Actions. Přerušené stahování se při dalším pokusu navazuje (-C -).
download() {
  local deadline=$((SECONDS + BUDGET))
  while [ $SECONDS -lt $deadline ]; do
    if curl -sSfL -C - --max-time $((deadline - SECONDS)) -o "$TMP/cz.osm.pbf" "$URL"; then
      return 0
    fi
    echo "osm_extract: stahování přerušeno, navazuji ($(du -m "$TMP/cz.osm.pbf" 2>/dev/null | cut -f1) MB)" >&2
    sleep 5
  done
  return 1
}

if [ -s "$MTB_PBF" ]; then
  echo "osm_extract: používám vyfiltrovaný výřez z cache ($MTB_PBF)"
else
  mkdir -p "$(dirname "$MTB_PBF")"
  rm -f "$TMP/cz.osm.pbf"
  if download; then
    osmium tags-filter "$TMP/cz.osm.pbf" \
      w/mtb:scale w/mtb:scale:imba w/mtb:scale:dh w/mtb w/bicycle=designated w/highway=cycleway \
      w/aerialway nwr/amenity=parking r/route=mtb \
      nwr/sport=pump_track nwr/leisure=track,pitch,sports_centre,bike_park \
      nwr/sport=mtb,downhill,bmx n/place=town,village \
      -o "$TMP/mtb-tags.osm.pbf" --overwrite
    # Kolem parků navíc všechny pěšiny a lesní cesty: část lajn má jen název a oneway (bez
    # MTB značek) a park_osm.py je přibere jako pokračování lajny se stejným názvem; po
    # síti cest se hledají i spojky mezi kusy lajny (výjezd vede kus po lesní cestě).
    python3 "$(dirname "$0")/park_area.py" > "$TMP/parks-area.geojson"
    osmium extract -p "$TMP/parks-area.geojson" "$TMP/cz.osm.pbf" -o "$TMP/near.osm.pbf" --overwrite
    osmium tags-filter "$TMP/near.osm.pbf" w/highway=path,track,cycleway,footway,bridleway -o "$TMP/near-paths.osm.pbf" --overwrite
    osmium merge "$TMP/mtb-tags.osm.pbf" "$TMP/near-paths.osm.pbf" -o "$MTB_PBF" --overwrite
    rm -f "$TMP/cz.osm.pbf" "$TMP/near.osm.pbf" "$TMP/near-paths.osm.pbf" "$TMP/mtb-tags.osm.pbf"
  elif [ -n "$FALLBACK" ] && [ -s "$FALLBACK" ]; then
    echo "::warning::Geofabrik nestihl za ${BUDGET} s, používám starší výřez ($FALLBACK)"
    rm -f "$TMP/cz.osm.pbf"
    SRC=$FALLBACK  # MTB_PBF zůstane prázdné, takže se starší výřez neuloží pod dnešní klíč
  else
    echo "osm_extract: Geofabrik nestihl za ${BUDGET} s a starší výřez není" >&2
    exit 1
  fi
fi
osmium fileinfo -g header.option.osmosis_replication_timestamp "$SRC" | sed 's/^/OSM data k: /' || true
python3 "$(dirname "$0")/osm_extract.py" "$SRC" --out "$OUT" ${PUMP:+--pumptracks "$PUMP"} ${COUNTRY:+--country "$COUNTRY"}
