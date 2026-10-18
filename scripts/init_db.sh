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

# Same rule as src/fbdb/schema.py: every sql/*.sql in byte order (so a new file can never be forgotten here);
# an existing database skips 001 (core tables are never re-run), all other files are idempotent.
mapfile -t files < <(LC_ALL=C ls sql/*.sql)
if [ "$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('public.companies') IS NOT NULL")" = "t" ]; then
  echo "Existing schema detected: upgrading with the idempotent files only."
  mapfile -t files < <(printf '%s\n' "${files[@]}" | grep -v '/001_schema\.sql$')
else
  echo "Empty database: creating the full schema."
fi
for f in "${files[@]}"; do run "$f"; done
echo "Schema ready."
