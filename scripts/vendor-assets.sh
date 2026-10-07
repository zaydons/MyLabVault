#!/usr/bin/env bash
# Downloads the front-end libraries MyLabVault serves from app/static/vendor, so pages
# work without internet access and no third-party CDN sees page views.
# To upgrade a library, change its version below, run this script, and test the UI.
set -euo pipefail

DEST="$(cd "$(dirname "$0")/.." && pwd)/app/static/vendor"
JSD="https://cdn.jsdelivr.net/npm"
DT="https://cdn.datatables.net"

FILES=(
  "jquery/jquery.min.js                      $JSD/jquery@3.7.1/dist/jquery.min.js"
  "bootstrap/bootstrap.bundle.min.js         $JSD/bootstrap@4.6.2/dist/js/bootstrap.bundle.min.js"
  "admin-lte/adminlte.min.css                $JSD/admin-lte@3.2.0/dist/css/adminlte.min.css"
  "admin-lte/adminlte.min.js                 $JSD/admin-lte@3.2.0/dist/js/adminlte.min.js"
  "chart.js/chart.umd.js                     $JSD/chart.js@4.4.0/dist/chart.umd.js"
  "datatables/dataTables.min.js              $DT/2.3.2/js/dataTables.min.js"
  "datatables/dataTables.bootstrap4.min.js   $DT/2.3.2/js/dataTables.bootstrap4.min.js"
  "datatables/dataTables.bootstrap4.min.css  $DT/2.3.2/css/dataTables.bootstrap4.min.css"
  "datatables/dataTables.responsive.min.js   $DT/responsive/3.0.5/js/dataTables.responsive.min.js"
  "datatables/responsive.bootstrap4.min.js   $DT/responsive/3.0.5/js/responsive.bootstrap4.min.js"
  "datatables/responsive.bootstrap4.min.css  $DT/responsive/3.0.5/css/responsive.bootstrap4.min.css"
  "mdi/css/materialdesignicons.min.css       $JSD/@mdi/font@7.4.47/css/materialdesignicons.min.css"
  "mdi/fonts/materialdesignicons-webfont.woff2 $JSD/@mdi/font@7.4.47/fonts/materialdesignicons-webfont.woff2"
  "mdi/fonts/materialdesignicons-webfont.woff  $JSD/@mdi/font@7.4.47/fonts/materialdesignicons-webfont.woff"
)
# IBM Plex Sans (SIL OFL 1.1), the weights the UI uses, Latin and Latin Extended subsets
for weight in 400 500 600 700; do
  for subset in latin latin-ext; do
    FILES+=("fonts/ibm-plex-sans-$subset-$weight-normal.woff2 $JSD/@fontsource/ibm-plex-sans@5.1.1/files/ibm-plex-sans-$subset-$weight-normal.woff2")
  done
done

for entry in "${FILES[@]}"; do
  read -r path url <<<"$entry"
  mkdir -p "$DEST/$(dirname "$path")"
  curl -sSfL --retry 3 -o "$DEST/$path" "$url"
  echo "$path"
done

# Only the woff2/woff icon fonts are shipped; point the icon CSS at just those.
python3 - "$DEST/mdi/css/materialdesignicons.min.css" <<'PY'
import re, sys
path = sys.argv[1]
css = open(path, encoding="utf-8").read()
src = ('src:url("../fonts/materialdesignicons-webfont.woff2?v=7.4.47") format("woff2"),'
       'url("../fonts/materialdesignicons-webfont.woff?v=7.4.47") format("woff");')
css, count = re.subn(r'src:url\("\.\./fonts/materialdesignicons-webfont\.eot[^;]*;src:[^;]*;', src, css, count=1)
assert count == 1, "icon font @font-face not found"
open(path, "w", encoding="utf-8").write(css)
PY

# @font-face rules for the IBM Plex Sans files above
LATIN="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
LATIN_EXT="U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"
{
  echo "/* IBM Plex Sans, SIL Open Font License 1.1 (via @fontsource/ibm-plex-sans 5.1.1) */"
  for weight in 400 500 600 700; do
    for subset in latin-ext latin; do
      range=$LATIN; [ "$subset" = latin-ext ] && range=$LATIN_EXT
      echo "@font-face{font-family:'IBM Plex Sans';font-style:normal;font-display:swap;font-weight:$weight;src:url(ibm-plex-sans-$subset-$weight-normal.woff2) format('woff2');unicode-range:$range}"
    done
  done
} > "$DEST/fonts/ibm-plex-sans.css"

(cd "$DEST" && find . -type f ! -name 'SHA256SUMS' ! -name '*.md' | sort | xargs sha256sum > SHA256SUMS)
echo "Vendored files written to $DEST"
