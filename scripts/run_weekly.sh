#!/usr/bin/env bash
# Weekly run wrapper. Cron example (Monday 03:00): 0 3 * * 1 /opt/fbdb/scripts/run_weekly.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
set -a; source .env; set +a
mkdir -p reports
LOG="reports/run_$(date +%F).log"
python -m fbdb.load.weekly >>"$LOG" 2>&1 || { echo "weekly run FAILED, see $LOG" >&2; exit 1; }
