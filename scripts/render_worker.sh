#!/usr/bin/env bash
# Worker main (runs as user "app"): schema upgrade -> optional one-time initial load -> scheduler (weekly run + daily check).
#   INITIAL_LOAD=1        run scripts/initial_load.sh once (marker file data/.initial_load_done); default 0
#   NAF_CSV_URL, LEGAL_FORMS_CSV_URL   optional: download the two small INSEE reference CSVs into data/raw if missing
# Failure of the initial load: alert, wait one hour, exit 1 (Render restarts the worker; downloads are skipped, loads are upserts).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?DATABASE_URL is not set (render.yaml wires it to the Render Postgres database)}"

echo "$(date -u +%FT%TZ) schema upgrade"
bash scripts/init_db.sh

MARKER=data/.initial_load_done
if [ "${INITIAL_LOAD:-0}" = 1 ] && [ ! -f "$MARKER" ]; then
  mkdir -p data/raw
  [ -n "${NAF_CSV_URL:-}" ] && [ ! -f data/raw/naf_rev2.csv ] && curl -fSL --retry 3 -o data/raw/naf_rev2.csv "$NAF_CSV_URL"
  [ -n "${LEGAL_FORMS_CSV_URL:-}" ] && [ ! -f data/raw/categories_juridiques.csv ] && curl -fSL --retry 3 -o data/raw/categories_juridiques.csv "$LEGAL_FORMS_CSV_URL"
  echo "$(date -u +%FT%TZ) initial load starting (hours expected)"
  if bash scripts/initial_load.sh; then
    date -u +%FT%TZ > "$MARKER"
    rm -f data/raw/Stock*.csv            # free ~25 GB of disk
    echo "$(date -u +%FT%TZ) initial load done"
  else
    python -m fbdb.ops.notify --subject "FBDB initial load FAILED on Render" --body "See reports/initial_load_*.log on the worker disk. The worker retries in 1 hour." || true
    sleep 3600
    exit 1
  fi
fi

if [ "${INITIAL_LOAD:-0}" != 1 ] && [ ! -f "$MARKER" ]; then
  echo "WARNING: initial load has not been done (set INITIAL_LOAD=1 once). The weekly job runs only against loaded data."
fi

exec python -m fbdb.ops.schedule
