# 15. Deployment, scheduling, logs, monitoring, alerts (Chunk 6)

Everything below is written but **not yet executed on a real server** (the build sandbox has no Docker and no access to the
data sources). Run the acceptance steps at the end on the real VPS before calling it done.

## 1. Server
- **Pilot / first 100k rows:** 2 vCPU, 4 GB RAM, 40 GB SSD is enough.
- **Full load (1M+ establishments, geocoding, enrichment):** 8 vCPU, 32 GB RAM, 100+ GB SSD (sizing in `04-architecture.md`;
  cost range in `11-timeline-cost.md`). Prices change: get a current quote.
- **Provider:** any EU VPS provider (for example Hetzner Cloud or OVHcloud). Choose an EU region: the data concerns French
  companies and directors (GDPR, see `09-security-privacy.md`). Ubuntu 24.04 LTS. The client owns the account, or pays for it.
- Hardening: SSH keys only, firewall allowing only SSH, no database port exposed (compose binds it to 127.0.0.1), unattended security updates.

## 2. Install
```
sudo apt-get install -y docker.io docker-compose-v2 git
git clone https://github.com/syntralogic/france-business-hubspot-db.git /opt/fbdb && cd /opt/fbdb
cp .env.example .env && chmod 600 .env       # fill POSTGRES_PASSWORD (add this line), INSEE_API_KEY and the optional keys
mkdir -p reports data backups && sudo chown -R 1000:1000 reports data     # the container runs as uid 1000
docker compose up -d db
docker compose --profile jobs run --rm app bash scripts/init_db.sh
```
`.env` needs `POSTGRES_PASSWORD=...` (any strong value); `DATABASE_URL` is built by compose. Secrets live only in `.env`
(git-ignored, not copied into the image). The GitHub token used for the build must be revoked and replaced by a deploy key.

## 3. Scheduler
`deploy/crontab.example` has three jobs: the weekly run (Monday 03:00), a daily monitoring check, a weekly database backup.
Install with `crontab -e`. Cron runs on the host and starts the container with `docker compose run`.

## 4. Logs
- One file per run: `reports/run_YYYY-MM-DD.log` with START/OK/FAIL/SKIP lines per step and each step's own output. Kept 90 days.
- Docker logs of the database are rotated (10 MB x 5). Cron output: `/var/log/fbdb-cron.log` (add a logrotate entry).
- Every run is also a row in `job_runs` (status, failed steps, log path); step details are in `import_export_batches`.

## 5. Monitoring (did the job run?)
- `python -m fbdb.ops.jobs check --job weekly --max-age-days 8` (daily cron): alerts when no run was recorded in 8 days, or the last run
  failed or was partial. This catches cron that never fired.
- Optional but recommended: set `HEALTHCHECK_PING_URL` to an external dead-man's-switch (for example a healthchecks.io check with a
  weekly schedule). Only an external check notices a server that is down, because then nothing local can alert.

## 6. Alerts
Set one or both in `.env`: `SLACK_WEBHOOK_URL`, or `SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/ALERT_EMAIL_FROM/ALERT_EMAIL_TO`.
A failing run sends the last 40 log lines. A broken channel is logged and never hides the original failure.
Test: `docker compose --profile jobs run --rm app python -m fbdb.ops.notify --subject "FBDB test alert" --body "hello"`.

## 7. Acceptance on the server (do all, in order)
1. `scripts/init_db.sh` ends with "Schema ready."
2. Alert test above arrives on Slack and/or email.
3. `python -m fbdb.ops.jobs check --job weekly` before any run prints `never` and sends an alert (proves alerting works).
4. Load reference data and a SIRENE sample (README quick start), then one full `scripts/run_weekly.sh` by hand: exit code 0, log has
   one OK/SKIP line per step, `job_runs` has a row with status `ok`, the check from step 3 now prints `ok`.
5. Break one step on purpose (for example a wrong `INPI_PASSWORD`): status `partial`, alert received, other steps still ran. Fix it.
6. Wait for the first real cron run and read its log.
7. Restore test: load the latest dump into a scratch database.
