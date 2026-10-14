#!/usr/bin/env bash
# One-time production load: reference data + full SIRENE stock files (active units only).
# Run it INSIDE the app container, after scripts/init_db.sh:
#   docker compose --profile jobs run --rm app bash scripts/initial_load.sh
# Needs ~25 GB free in data/raw, internet access and DATABASE_URL (set by docker compose).
# Reference CSVs (INSEE NAF rev.2 labels and legal-form list) are NOT downloaded here: put them in data/raw/ first as
# naf_rev2.csv and categories_juridiques.csv (see README quick start), or set NAF_CSV / LEGAL_FORMS_CSV to other paths.
# Idempotent: downloads are skipped when the files already exist; loads are upserts. Delete data/raw/*.csv afterwards to free disk.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?DATABASE_URL is not set}"
WORK="${1:-data/raw}"; mkdir -p "$WORK" reports
BASE="${SIRENE_STOCK_BASE:-https://files.data.gouv.fr/insee-sirene}"
NAF="${NAF_CSV:-$WORK/naf_rev2.csv}"; LEGAL="${LEGAL_FORMS_CSV:-$WORK/categories_juridiques.csv}"
LOG="reports/initial_load_$(date +%F).log"
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

[ -f "$NAF" ] && [ -f "$LEGAL" ] || { say "FAIL missing reference CSVs ($NAF, $LEGAL)"; exit 2; }
for f in StockUniteLegale StockEtablissement; do
  if [ ! -f "$WORK/${f}_utf8.csv" ]; then
    say "download $f"
    curl -fSL --retry 5 --retry-delay 10 -o "$WORK/$f.zip" "$BASE/${f}_utf8.zip"
    unzip -oq "$WORK/$f.zip" -d "$WORK" && rm "$WORK/$f.zip"
  fi
done
say "load reference data"
python -m fbdb.load.reference --naf "$NAF" --legal-forms "$LEGAL" >>"$LOG" 2>&1
say "load SIRENE stock (long step: expect hours on the full file)"
python -m fbdb.load.sirene_loader --ul "$WORK/StockUniteLegale_utf8.csv" --etab "$WORK/StockEtablissement_utf8.csv" --only-active >>"$LOG" 2>&1
psql "$DATABASE_URL" -Atc "select 'companies', count(*) from companies union all select 'establishments', count(*) from company_addresses" | tee -a "$LOG"
say "initial load done. Next: scripts/run_weekly.sh (first delta), then python -m fbdb.report.metrics"
