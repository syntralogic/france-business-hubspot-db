#!/usr/bin/env bash
# Database backup on the HOST (cron): custom-format dump into ./backups, keep 28 days. Copy the folder off the server too.
# Usage: cd /opt/fbdb && scripts/backup.sh        Restore check: scripts/restore_test.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
OUT="backups/fbdb_$(date +%F).dump"
docker compose exec -T db pg_dump -U fbdb -Fc fbdb > "$OUT.tmp"
[ -s "$OUT.tmp" ] || { echo "backup is empty" >&2; rm -f "$OUT.tmp"; exit 1; }
mv "$OUT.tmp" "$OUT"
find backups -name 'fbdb_*.dump' -mtime +28 -delete
echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"
