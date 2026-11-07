#!/usr/bin/env bash
# Stáhne OSM výřez Česka (Geofabrik, aktualizovaný denně), vyfiltruje MTB cesty, lanovky,
# parkoviště a relace route=mtb a vytáhne data kolem parků do JSON ve tvaru odpovědi Overpass.
#   scripts/osm_extract.sh výstup.json
# Na GitHub runneru doinstaluje osmium-tool a pyosmium (apt).
set -euo pipefail
OUT=${1:?výstupní JSON}
TMP=${RUNNER_TEMP:-/tmp}
URL=https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf

if ! command -v osmium >/dev/null || ! python3 -c 'import osmium' 2>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq osmium-tool python3-pyosmium >/dev/null
fi

curl -sSfL --retry 3 -o "$TMP/cz.osm.pbf" "$URL"
osmium fileinfo -g header.option.osmosis_replication_timestamp "$TMP/cz.osm.pbf" | sed 's/^/OSM data k: /' || true
osmium tags-filter "$TMP/cz.osm.pbf" \
  w/mtb:scale w/mtb:scale:imba w/mtb:scale:dh w/mtb=designated w/bicycle=designated \
  w/aerialway nwr/amenity=parking r/route=mtb \
  -o "$TMP/mtb.osm.pbf" --overwrite
python3 "$(dirname "$0")/osm_extract.py" "$TMP/mtb.osm.pbf" --out "$OUT"
