#!/usr/bin/env bash
# Weekly run wrapper. Cron example (Monday 03:00): 0 3 * * 1 /opt/fbdb/scripts/run_weekly.sh
# Every step runs even if an earlier one failed (they are independent enough: a failed delta must not block the
# bounded enrichments). At the end: one row in job_runs, a failure alert (Slack/email) and an optional ping.
# Exit code: 0 all steps ok, 1 otherwise. Steps without their key/credentials are skipped and logged.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .venv/bin/activate ] && source .venv/bin/activate
if [ -f .env ]; then set -a; source .env; set +a; fi
mkdir -p reports
LOG="reports/run_$(date +%F).log"
STARTED="$(date -u +%FT%TZ)"
FAILED=()

step() {  # step NAME CMD...
  local name="$1"; shift
  echo "$(date -u +%FT%TZ) START $name" >>"$LOG"
  if "$@" >>"$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) OK    $name" >>"$LOG"
  else
    echo "$(date -u +%FT%TZ) FAIL  $name (exit $?)" >>"$LOG"
    FAILED+=("$name")
  fi
}
skip() { echo "$(date -u +%FT%TZ) SKIP  $1 ($2)" >>"$LOG"; }

step weekly_delta python -m fbdb.load.weekly
# Directors refresh (Annuaire des Entreprises). Bounded per run; companies are re-checked every 30 days.
step directors python -m fbdb.enrich.directors --limit "${DIRECTORS_PER_RUN:-5000}"
step geocode python -m fbdb.enrich.geocode --limit "${GEOCODE_PER_RUN:-200000}"
step bodacc python -m fbdb.enrich.bodacc --days "${BODACC_DAYS:-8}"
if [ -n "${INPI_USERNAME:-}" ]; then step inpi python -m fbdb.enrich.inpi --limit "${INPI_PER_RUN:-500}"; else skip inpi "INPI_USERNAME not set"; fi
if [ -n "${PAPPERS_API_KEY:-}" ]; then step pappers python -m fbdb.enrich.pappers --limit "${PAPPERS_PER_RUN:-200}"; else skip pappers "PAPPERS_API_KEY not set"; fi
if [ -n "${GOOGLE_PLACES_API_KEY:-}" ]; then step places python -m fbdb.enrich.places --limit "${PLACES_PER_RUN:-100}"; else skip places "GOOGLE_PLACES_API_KEY not set"; fi

# Housekeeping: keep 90 days of logs
find reports -maxdepth 1 -name 'run_*.log' -mtime +90 -delete 2>/dev/null || true

if [ ${#FAILED[@]} -eq 0 ]; then
  STATUS=ok
elif [[ " ${FAILED[*]} " == *" weekly_delta "* ]] || [ ${#FAILED[@]} -ge 4 ]; then
  STATUS=failed
else
  STATUS=partial
fi
FAILED_CSV="$(IFS=,; echo "${FAILED[*]:-}")"
python -m fbdb.ops.jobs record --job weekly --status "$STATUS" --started "$STARTED" --failed-steps "$FAILED_CSV" --log "$LOG" >>"$LOG" 2>&1 \
  || echo "$(date -u +%FT%TZ) WARN  could not record job_runs row" >>"$LOG"
if [ "$STATUS" = ok ]; then
  python -m fbdb.ops.notify --ping ok >>"$LOG" 2>&1 || true
  echo "weekly run OK, log $LOG"
  exit 0
fi
python -m fbdb.ops.notify --ping fail --subject "FBDB weekly run $STATUS: ${FAILED_CSV}" --body-file "$LOG" --tail 40 >>"$LOG" 2>&1 || true
echo "weekly run $STATUS (failed: ${FAILED_CSV}), see $LOG" >&2
exit 1
