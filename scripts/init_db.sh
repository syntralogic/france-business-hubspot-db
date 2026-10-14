#!/usr/bin/env bash
# Create or upgrade the PostgreSQL schema. Safe to run again.
#   fresh database    -> applies every sql/*.sql file in order
#   existing database -> re-applies only the idempotent files (reference data, staging, state, view,
#                        directors bookkeeping, closure-date upgrade); 001 (core tables) is never re-run
# Usage: DATABASE_URL=postgresql://user:pw@host/fbdb scripts/init_db.sh   (or put it in .env)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${DATABASE_URL:-}" ] && [ -f .env ]; then set -a; source .env; set +a; fi
: "${DATABASE_URL:?DATABASE_URL is not set (see .env.example)}"

export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"   # hide "already exists, skipping" notices
run() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$1"; echo "applied $1"; }

if [ "$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('public.companies') IS NOT NULL")" = "t" ]; then
  echo "Existing schema detected: upgrading with the idempotent files only."
  files=(sql/000_reference_data.sql sql/002_staging.sql sql/003_pipeline_state.sql sql/004_export_view.sql
         sql/005_directors_enrichment.sql sql/006_closure_date.sql sql/007_geocoding.sql sql/008_bodacc.sql sql/009_inpi.sql sql/010_paid_enrichment.sql sql/011_ops.sql)
else
  echo "Empty database: creating the full schema."
  files=(sql/000_reference_data.sql sql/001_schema.sql sql/002_staging.sql sql/003_pipeline_state.sql
         sql/004_export_view.sql sql/005_directors_enrichment.sql sql/006_closure_date.sql sql/007_geocoding.sql sql/008_bodacc.sql sql/009_inpi.sql sql/010_paid_enrichment.sql sql/011_ops.sql)
fi
for f in "${files[@]}"; do run "$f"; done
echo "Schema ready."
