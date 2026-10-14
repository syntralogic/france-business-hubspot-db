#!/usr/bin/env bash
# Update an existing server install to the latest main. Run on the HOST in /opt/fbdb (never during a weekly run).
#   scripts/deploy.sh          pull, rebuild image, apply schema upgrades, run the quick self-check
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose build app
docker compose up -d db
docker compose --profile jobs run --rm app bash scripts/init_db.sh
docker compose --profile jobs run --rm app python -m fbdb.ops.jobs check --job weekly --max-age-days 8 || true
echo "deployed $(git rev-parse --short HEAD)"
