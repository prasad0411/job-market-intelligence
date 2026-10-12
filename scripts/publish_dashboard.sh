#!/usr/bin/env bash
# Publishes a fresh public snapshot of the dashboard to the gh-pages branch (served by GitHub Pages).
# Only public postings and pipeline statistics are exported. The gh-pages branch keeps a single
# commit, so daily snapshots never bloat the repository history.
#   bash scripts/publish_dashboard.sh            build and publish
#   bash scripts/publish_dashboard.sh --dry-run  build and serve locally, publish nothing
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
BASE="/job-market-intelligence/"
echo "[$(date '+%F %T')] publish_dashboard start"

mkdir -p dashboard-web/public
rm -rf dashboard-web/public/data
./venv/bin/python -m dashboard_api.export_snapshot dashboard-web/public/data --bigquery
(cd dashboard-web && VITE_DATA_MODE=static npm run build -- --base="$BASE" >/dev/null)

SITE=$(mktemp -d)
trap 'rm -rf "$SITE"; [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null || true' EXIT
mkdir -p "$SITE${BASE}"
cp -R dashboard-web/dist/. "$SITE${BASE}"
touch "$SITE${BASE}.nojekyll"

# smoke test the exact files that will be served
python3 -m http.server 8790 --directory "$SITE" >/dev/null 2>&1 &
SRV=$!
sleep 1
curl -fsS "http://127.0.0.1:8790${BASE}" -o "$SITE/.index.check"
grep -q "${BASE}assets/" "$SITE/.index.check" || { echo "FAIL: index does not reference ${BASE}assets"; exit 1; }
for f in summary jobs pipeline runs quarantine meta; do
  curl -fsS -o /dev/null "http://127.0.0.1:8790${BASE}data/$f.json" || { echo "FAIL: data/$f.json not served"; exit 1; }
done
JS=$(grep -o "${BASE}assets/[^\"]*\.js" "$SITE/.index.check" | head -1)
curl -fsS -o /dev/null "http://127.0.0.1:8790$JS" || { echo "FAIL: bundle $JS not served"; exit 1; }
rm -f "$SITE/.index.check"
echo "  PASS  static site smoke test (index, bundle, 6 data files)"
kill "$SRV" 2>/dev/null || true; SRV=""

if [ "${1:-}" = "--dry-run" ]; then echo "  dry run: nothing published"; exit 0; fi

REMOTE=$(git remote get-url origin)
NAME=$(git config user.name || echo "Prasad Kanade"); EMAIL=$(git config user.email || echo "kanade.pra@northeastern.edu")
cd "$SITE${BASE}"
git init -q -b gh-pages
git add -A
git -c user.name="$NAME" -c user.email="$EMAIL" commit -qm "Dashboard snapshot $(date -u +%FT%TZ)"
git push -fq "$REMOTE" gh-pages
echo "[$(date '+%F %T')] published"
