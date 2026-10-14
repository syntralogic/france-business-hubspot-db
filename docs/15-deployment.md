# 15. Deployment runbook (Appendix C)

Status: the shell scripts were tested against a local PostgreSQL 16 (schema create/upgrade twice, wrapper failure handling, step
timeout, export on an empty database). **Not yet executed:** Docker itself, the live INSEE/Annuaire/BODACC/geocoding calls and the full
SIRENE load (the build sandbox has no Docker and limited network). Do the acceptance checklist in section 10 on the real server.

## 0. What you need before starting
| Item | Who | Notes |
|---|---|---|
| VPS in the EU (Ubuntu 24.04) | client pays, developer sets up | pilot 2 vCPU / 4 GB / 40 GB; full load 8 vCPU / 32 GB / 100+ GB |
| INSEE API key | developer registers (free) | portail-api.insee.fr, subscribe to "API Sirene" |
| `naf_rev2.csv`, `categories_juridiques.csv` | developer downloads from insee.fr | see README quick start; put in `data/raw/` |
| Slack webhook or SMTP account | client/developer | failure alerts |
| healthchecks.io (or similar) URL | developer | external dead-man's switch |
| Phone/email licence, Pappers, Google Places, INPI | client decision | optional, see `16-client-questions.md` |

## 1. Server hardening
SSH keys only, firewall allowing only SSH (22), unattended security updates, no database port exposed (compose binds 127.0.0.1),
non-root user for day-to-day work. The client owns the cloud account or pays for it.

## 2. Install
```
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo git clone https://github.com/syntralogic/france-business-hubspot-db.git /opt/fbdb && sudo chown -R $USER /opt/fbdb && cd /opt/fbdb
cp .env.example .env && chmod 600 .env     # set POSTGRES_PASSWORD, INSEE_API_KEY, alert settings; on a big server also the PG_* values
mkdir -p reports data/raw data/staging data/exports backups && sudo chown -R 1000:1000 reports data backups   # the container runs as uid 1000
docker compose build app
docker compose up -d db
docker compose --profile jobs run --rm app bash scripts/init_db.sh          # ends with "Schema ready."
```
If the repository is private, clone with a read-only **deploy key** (repo Settings, Deploy keys), never a personal token. `.env` is
git-ignored and excluded from the image (`.dockerignore`); the container receives it through compose `env_file`.

## 3. First load (one time)
```
# copy naf_rev2.csv and categories_juridiques.csv into data/raw/ first
docker compose --profile jobs run --rm app bash scripts/initial_load.sh
```
Downloads the two SIRENE stock files (about 25 GB free needed), loads reference data and all active units, prints row counts.
Expect hours for the full file; run it inside `tmux`/`screen`. Log: `reports/initial_load_<date>.log`. Afterwards `rm data/raw/Stock*.csv`.
Then run `python -m fbdb.report.metrics --out reports/metrics` to measure coverage and fill rates.

## 4. Weekly run and scheduler
`deploy/crontab.example` installs with `crontab -e` (adjust `/opt/fbdb`): Monday 03:00 weekly run, daily 09:30 monitoring check,
Sunday 01:00 backup, monthly restore test. A weekly run does, in order: SIRENE delta, directors, geocoding, BODACC, INPI (if
credentials), Pappers (if key), Places (if key), then the **HubSpot export** into `data/exports/<date>/` (CSV files plus
`hubspot_errors_<date>.csv`). Every step is independent: one failure never blocks the others, and each step is capped by
`STEP_TIMEOUT_SECONDS` (default 6 h). Result: one row in `job_runs` (`ok`, `partial` or `failed`) and an alert on failure.
Export switches (in `.env`): `EXPORT_AFTER_WEEKLY`, `EXPORT_INCLUDE_CLOSED` (adds closures for HubSpot status updates),
`EXPORT_SIRET_IS_IMPORT_KEY`.

## 5. Importing into HubSpot (every week, manual unless the client's plan allows the API)
1. Take the newest `data/exports/<date>/` files and read `hubspot_errors_<date>.csv`; fix or accept rejected rows.
2. Follow `docs/07-hubspot-mapping.md` (pre-import checklist, custom properties created once, import key, UTF-8).
3. HubSpot: Import, "Update existing records and create new ones", match on the unique SIRET property.
4. Keep the HubSpot import error file next to the export for audit.

## 6. Updating the code later
```
cd /opt/fbdb && scripts/deploy.sh          # git pull --ff-only, rebuild image, schema upgrade (idempotent), monitoring self-check
```
Do not deploy during the Monday run. CI (`.github/workflows/ci.yml`) runs all tests against PostgreSQL 16 and builds the image on
every push, so deploy only commits with a green check.

## 7. Logs
`reports/run_YYYY-MM-DD.log` (START/OK/FAIL/SKIP per step, kept 90 days), `reports/initial_load_*.log`, `/var/log/fbdb-cron.log`
(add a logrotate entry), Docker logs for the database (10 MB x 5). Every run is also a row in `job_runs`; step details are in
`import_export_batches`.

## 8. Monitoring and alerts
- Daily `python -m fbdb.ops.jobs check --job weekly --max-age-days 8` alerts when no run was recorded or the last run failed or was partial.
- `HEALTHCHECK_PING_URL` (healthchecks.io style, weekly schedule): the only way to notice a dead server.
- Alerts: `SLACK_WEBHOOK_URL` and/or SMTP settings. Test: `docker compose --profile jobs run --rm app python -m fbdb.ops.notify --subject "FBDB test alert" --body "hello"`.

## 9. Backups and restore
`scripts/backup.sh` (Sunday): `pg_dump -Fc` into `backups/`, keeps 28 days; copy the folder off the server (object storage or another host).
`scripts/restore_test.sh` (monthly): restores the newest dump into a scratch database, compares the `companies` count, drops it.
Disaster recovery: new server, steps 2, then `docker compose exec -T db pg_restore -U fbdb -d fbdb --clean --if-exists < backups/<file>`.

## 10. Acceptance on the server (do all, in order)
1. `scripts/init_db.sh` ends with "Schema ready."; running it twice is harmless.
2. Test alert arrives on Slack and/or email.
3. `python -m fbdb.ops.jobs check --job weekly` before any run prints `never` and alerts.
4. `PYTHONPATH=src python scripts/live_smoke.py` passes (needs `INSEE_API_KEY`; record in `13-verification-log.md`).
5. `scripts/initial_load.sh` completes; counts look plausible versus the SIRENE stock.
6. One manual `scripts/run_weekly.sh`: exit 0, one OK/SKIP line per step, `job_runs` row `ok`, export files present, check from step 3 prints `ok`.
7. Run it a second time: 0 new history rows (idempotent).
8. Break one step on purpose (wrong `INPI_PASSWORD`): status `partial`, alert received, other steps still ran. Fix it.
9. `scripts/backup.sh` then `scripts/restore_test.sh` succeed.
10. Test import of 100+ rows into a HubSpot sandbox: expected records, no unintended merges, accents intact.
11. Read the first real cron run's log the next Monday.

## 11. Troubleshooting
| Symptom | Check |
|---|---|
| `POSTGRES_PASSWORD` error on `compose up` | variable missing in `.env` |
| Permission denied writing `reports/` or `data/` | `sudo chown -R 1000:1000 reports data backups` |
| Step FAIL with exit 124 | step timeout hit: source API down or slow; raise `STEP_TIMEOUT_SECONDS` only if the step is genuinely long |
| `weekly_delta` fails with 401/403 | `INSEE_API_KEY` missing, expired or not subscribed to API Sirene |
| Database out of memory on load | lower the `PG_*` values or use a bigger server |
| No alert received | run the alert test (section 8); a broken channel is logged, never hides the failure |
