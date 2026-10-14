#!/usr/bin/env bash
# Proves the newest backup restores: loads it into a scratch database and compares the companies count. Run on the HOST.
set -euo pipefail
cd "$(dirname "$0")/.."
DUMP="$(ls -1t backups/fbdb_*.dump | head -1)"; [ -n "$DUMP" ] || { echo "no dump found" >&2; exit 1; }
docker compose exec -T db psql -U fbdb -d postgres -qc "DROP DATABASE IF EXISTS fbdb_restore_test" -c "CREATE DATABASE fbdb_restore_test"
docker compose exec -T db pg_restore -U fbdb -d fbdb_restore_test --no-owner < "$DUMP"
live="$(docker compose exec -T db psql -U fbdb -d fbdb -Atc 'select count(*) from companies')"
rest="$(docker compose exec -T db psql -U fbdb -d fbdb_restore_test -Atc 'select count(*) from companies')"
docker compose exec -T db psql -U fbdb -d postgres -qc "DROP DATABASE fbdb_restore_test"
echo "dump $DUMP: companies live=$live restored=$rest"
[ "$rest" -gt 0 ] || { echo "RESTORE CHECK FAILED" >&2; exit 1; }
