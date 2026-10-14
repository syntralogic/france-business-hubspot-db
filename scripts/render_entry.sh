#!/usr/bin/env bash
# Container entrypoint on Render (runs as root): prepare the persistent disk, then drop to user "app".
# The disk is mounted at /app/data (render.yaml). Everything that must survive a redeploy lives under it.
set -euo pipefail
cd /app
mkdir -p data/raw data/staging data/exports data/reports
ln -sfn data/reports reports            # run logs persist on the disk too
chown -R 1000:1000 data
chown -h 1000:1000 reports
# setpriv keeps the whole environment (secrets set in the Render dashboard) unchanged
exec setpriv --reuid=1000 --regid=1000 --init-groups env HOME=/home/app bash scripts/render_worker.sh
