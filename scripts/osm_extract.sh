#!/usr/bin/env bash
# Stáhne OSM výřez Česka (Geofabrik, aktualizovaný denně), vyfiltruje MTB cesty, lanovky,
# parkoviště a relace route=mtb a vytáhne data kolem parků do JSON ve tvaru odpovědi Overpass.
#   scripts/osm_extract.sh výstup.json [pumptracky.geojson] [cela-cr.json]
# MTB_PBF=cesta: vyfiltrovaný výřez; když existuje, přeskočí se stažení (cache po dnech v Actions).
# Na GitHub runneru doinstaluje osmium-tool a pyosmium (apt).
set -euo pipefail
OUT=${1:?výstupní JSON}
PUMP=${2:-}
COUNTRY=${3:-}
TMP=${RUNNER_TEMP:-/tmp}
URL=https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf

if ! command -v osmium >/dev/null || ! python3 -c 'import osmium' 2>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq osmium-tool python3-pyosmium >/dev/null
fi

MTB_PBF=${MTB_PBF:-$TMP/mtb.osm.pbf}
if [ -s "$MTB_PBF" ]; then
  echo "osm_extract: používám vyfiltrovaný výřez z cache ($MTB_PBF)"
else
  mkdir -p "$(dirname "$MTB_PBF")"
  curl -sSfL --retry 3 --max-time 1200 -o "$TMP/cz.osm.pbf" "$URL"
  osmium tags-filter "$TMP/cz.osm.pbf" \
    w/mtb:scale w/mtb:scale:imba w/mtb:scale:dh w/mtb w/bicycle=designated \
    w/aerialway nwr/amenity=parking r/route=mtb \
    nwr/sport=pump_track nwr/leisure=track,pitch,sports_centre,bike_park \
    nwr/sport=mtb,downhill,bmx n/place=town,village \
    -o "$MTB_PBF" --overwrite
  rm -f "$TMP/cz.osm.pbf"
fi
osmium fileinfo -g header.option.osmosis_replication_timestamp "$MTB_PBF" | sed 's/^/OSM data k: /' || true
python3 "$(dirname "$0")/osm_extract.py" "$MTB_PBF" --out "$OUT" ${PUMP:+--pumptracks "$PUMP"} ${COUNTRY:+--country "$COUNTRY"}
