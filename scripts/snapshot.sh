#!/usr/bin/env bash
# One-file snapshot of the WHOLE database (every company, every collected value with its source, date and proof, the memory tables that
# stop a paid lookup from being paid twice) written on the persistent disk: data/backups/fbdb_YYYY-MM-DD.dump (pg_dump custom format).
# Works inside the Render worker (DATABASE_URL, pg_dump from postgresql-client). The scripts/backup.sh of the VPS setup is the host-side twin.
# Variables: DATABASE_URL (required), SNAPSHOT_DIR (default data/backups), SNAPSHOT_KEEP_DAYS (default 28).
# Restore into an EMPTY database:  pg_restore --no-owner -d "$NEW_DATABASE_URL" data/backups/fbdb_YYYY-MM-DD.dump
# A snapshot is only kept when pg_restore can list its table data; a failed or empty dump never replaces a good one.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?DATABASE_URL is not set}"
DIR="${SNAPSHOT_DIR:-data/backups}"; KEEP="${SNAPSHOT_KEEP_DAYS:-28}"
mkdir -p "$DIR"
OUT="$DIR/fbdb_$(date +%F).dump"
cleanup() { rm -f "$OUT.tmp" "$OUT.err"; }
trap cleanup EXIT
if ! pg_dump -Fc --no-owner "$DATABASE_URL" > "$OUT.tmp" 2> "$OUT.err"; then
  echo "snapshot failed: $(tail -n 2 "$OUT.err" | sed -E 's#postgres(ql)?://[^ ]*#<url>#g')" >&2   # never echo the URL (it holds the password)
  exit 1
fi
[ -s "$OUT.tmp" ] || { echo "snapshot is empty" >&2; exit 1; }
# read the list into a variable first: `pg_restore --list | grep -q` ends the pipe at the first match and, with pipefail, a big archive
# would then look like a failure (SIGPIPE)
LIST="$(pg_restore --list "$OUT.tmp")" || { echo "snapshot cannot be read back by pg_restore" >&2; exit 1; }
grep -q "TABLE DATA" <<<"$LIST" || { echo "snapshot has no table data" >&2; exit 1; }
mv "$OUT.tmp" "$OUT"
find "$DIR" -name 'fbdb_*.dump' -mtime +"$KEEP" -delete
echo "snapshot written: $OUT ($(du -h "$OUT" | cut -f1)); kept $KEEP days. Copy the folder off the server too."
