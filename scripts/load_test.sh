#!/usr/bin/env bash
# Full-SIRENE load test (Chunk 1, steps 5-8). Run on a server with internet, PostgreSQL and INSEE_API_KEY.
# Usage: scripts/load_test.sh [WORKDIR]   (needs ~25 GB free in WORKDIR; default ./data/sirene)
# Uses the DATABASE_URL from .env: point it at a THROW-AWAY database, not production.
# Writes timings and row counts to reports/load_test_<date>.log, which is what to paste back for review.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
set -a; source .env; set +a
WORK="${1:-data/sirene}"; mkdir -p "$WORK" reports
LOG="reports/load_test_$(date +%F).log"
BASE="https://files.data.gouv.fr/insee-sirene"
stamp() { date +%T; }
run() { local name="$1"; shift; local t0=$SECONDS; echo "[$(stamp)] START $name" | tee -a "$LOG"
  /usr/bin/time -v "$@" >>"$LOG" 2>"$WORK/time_$name.txt" || { echo "FAILED $name" | tee -a "$LOG"; exit 1; }
  echo "[$(stamp)] DONE  $name in $((SECONDS - t0))s, peak RSS $(grep 'Maximum resident' "$WORK/time_$name.txt" | awk '{print $6}') kB" | tee -a "$LOG"; }

# 5. download the two stock files
for f in StockUniteLegale StockEtablissement; do
  [ -f "$WORK/${f}_utf8.csv" ] || { curl -fSL -o "$WORK/$f.zip" "$BASE/${f}_utf8.zip" && unzip -oq "$WORK/$f.zip" -d "$WORK" && rm "$WORK/$f.zip"; }
done
scripts/init_db.sh | tee -a "$LOG"

# 6. 100k-row sample load (header + first 100000 lines of each file; establishments may not all match their legal unit, fine for timing)
head -n 100001 "$WORK/StockUniteLegale_utf8.csv"  > "$WORK/ul_sample.csv"
head -n 100001 "$WORK/StockEtablissement_utf8.csv" > "$WORK/etab_sample.csv"
run sample_load python -m fbdb.load.sirene_loader --ul "$WORK/ul_sample.csv" --etab "$WORK/etab_sample.csv" --only-active

# 7. full load
run full_load python -m fbdb.load.sirene_loader --ul "$WORK/StockUniteLegale_utf8.csv" --etab "$WORK/StockEtablissement_utf8.csv" --only-active

# 8. weekly delta and export on the full data, then idempotence (second delta must add 0 history rows)
run weekly_1 python -m fbdb.load.weekly
run weekly_2 python -m fbdb.load.weekly
run export   python -m fbdb.export.run_export
psql "$DATABASE_URL" -Atc "select 'companies', count(*) from companies union all select 'change_history', count(*) from company_change_history" | tee -a "$LOG" || true
echo "Load test finished. Paste $LOG (and the peak RSS lines) back for review." | tee -a "$LOG"
