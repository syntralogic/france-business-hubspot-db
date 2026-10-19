#!/usr/bin/env bash
# Weekly run wrapper. Cron example (Monday 03:00): 0 3 * * 1 /opt/fbdb/scripts/run_weekly.sh
# Every step runs even if an earlier one failed (they are independent enough: a failed delta must not block the
# bounded enrichments). At the end: one row in job_runs, a failure alert (Slack/email) and an optional ping.
# STEP_TIMEOUT_SECONDS (default 21600 = 6 h) caps each step.
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
  timeout "${STEP_TIMEOUT_SECONDS:-21600}" "$@" >>"$LOG" 2>&1   # a hung API must not block the whole run (exit 124 = timeout)
  local rc=$?   # capture first: $(date) below would reset $? to 0
  if [ "$rc" -eq 0 ]; then
    echo "$(date -u +%FT%TZ) OK    $name" >>"$LOG"
  else
    echo "$(date -u +%FT%TZ) FAIL  $name (exit $rc)" >>"$LOG"
    FAILED+=("$name")
  fi
}
skip() { echo "$(date -u +%FT%TZ) SKIP  $1 ($2)" >>"$LOG"; }

step weekly_delta python -m fbdb.load.weekly
# Directors refresh (Annuaire des Entreprises). Bounded per run; companies are re-checked every 30 days.
step directors python -m fbdb.enrich.directors --limit "${DIRECTORS_PER_RUN:-5000}"
step geocode python -m fbdb.enrich.geocode --limit "${GEOCODE_PER_RUN:-200000}"
step bodacc python -m fbdb.enrich.bodacc --days "${BODACC_DAYS:-8}"
step register_events python -m fbdb.enrich.register_events --days "${REGISTER_EVENTS_DAYS:-365}"
if [ -n "${INPI_USERNAME:-}" ]; then step inpi python -m fbdb.enrich.inpi --limit "${INPI_PER_RUN:-500}"; else skip inpi "INPI_USERNAME not set"; fi
if [ -n "${PAPPERS_API_KEY:-}" ]; then step pappers python -m fbdb.enrich.pappers --limit "${PAPPERS_PER_RUN:-200}"; else skip pappers "PAPPERS_API_KEY not set"; fi
if [ -n "${GOOGLE_PLACES_API_KEY:-}" ]; then step places python -m fbdb.enrich.places --limit "${PLACES_PER_RUN:-100}"; else skip places "GOOGLE_PLACES_API_KEY not set"; fi

# HubSpot export (the deliverable). EXPORT_AFTER_WEEKLY=0 disables it; EXPORT_INCLUDE_CLOSED=1 adds closed establishments
# so HubSpot can be updated with closures; EXPORT_SIRET_IS_IMPORT_KEY=1 only if the HubSpot import uses SIRET as identifier.
if [ "${EXPORT_AFTER_WEEKLY:-1}" = 1 ]; then
  EXPORT_ARGS=(--out "data/exports/$(date +%F)")
  [ "${EXPORT_INCLUDE_CLOSED:-0}" = 1 ] && EXPORT_ARGS+=(--include-closed)
  [ "${EXPORT_SIRET_IS_IMPORT_KEY:-0}" = 1 ] && EXPORT_ARGS+=(--siret-is-import-key)
  step export python -m fbdb.export.run_export "${EXPORT_ARGS[@]}"
else skip export "EXPORT_AFTER_WEEKLY=0"; fi

# HubSpot Contacts export (people with their own email/phone/mobile; empty file set when nobody is known yet)
if [ "${EXPORT_AFTER_WEEKLY:-1}" = 1 ]; then
  step export_contacts python -m fbdb.export.hubspot_contacts --out "data/exports/$(date +%F)"
else skip export_contacts "EXPORT_AFTER_WEEKLY=0"; fi

# Housekeeping: keep 90 days of logs, 8 weeks of export folders
find data/exports -mindepth 1 -maxdepth 1 -type d -mtime +56 -exec rm -rf {} + 2>/dev/null || true
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
