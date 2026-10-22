# 17. Render deployment (Appendix E)

Status: written and checked on 14 Oct 2026 against Render's published Blueprint schema and docs. The scheduler and entrypoint were
tested locally (unit tests, SIGTERM handling, privilege drop). **Not yet run on Render itself:** do the checklist in section 7.

## 1. What gets created (`render.yaml`)
| Resource | Setting | Why |
|---|---|---|
| Render Postgres `fbdb-postgres` | region frankfurt, plan `basic-4gb`, 50 GB disk, autoscaling on, PG 16, no external access | EU data, explicit **paid** plan (a free Render database is deleted after 30 days) |
| Background Worker `fbdb-pipeline` | Docker (`Dockerfile.render`), plan `standard` (2 GB / 1 CPU), frankfurt | long-running process, no 12 h cap |
| Persistent disk `fbdb-data` | 60 GB at `/app/data` | raw SIRENE files (about 25 GB during the first load), HubSpot exports, run logs |

Why not a Render **Cron Job**: it cannot use a persistent disk (exports and downloads would vanish after every run) and is
stopped after 12 hours. So the worker runs its own scheduler (`python -m fbdb.ops.schedule`): weekly run Monday 03:00 UTC,
daily monitoring check 09:30 UTC. Change with `WEEKLY_DAY`, `WEEKLY_TIME`, `CHECK_TIME`.

Prices are not hard-coded here: check render.com/pricing for the database plan, the worker plan and the disk (disk was
$0.25 per GB per month when checked). Render's database and worker plans are billed monthly; budget for both plus the disk.

## 2. Before you start
1. Repo pushed to GitHub with `render.yaml`, `Dockerfile.render` and `scripts/render_*.sh` on the branch Render will use.
2. INSEE API key (portail-api.insee.fr, subscribe to "API Sirene").
3. The two small INSEE reference files `naf_rev2.csv` and `categories_juridiques.csv`. Either give Render two download URLs
   (`NAF_CSV_URL`, `LEGAL_FORMS_CSV_URL`; any raw file URL works) or upload them by SSH (section 4).
4. Optional: Slack webhook URL and a healthchecks.io ping URL.

## 3. Deploy
1. Render Dashboard, New, **Blueprint**, connect the GitHub repo, pick the branch. Render reads `render.yaml`.
2. Fill the prompted values: `INSEE_API_KEY`, `NAF_CSV_URL`, `LEGAL_FORMS_CSV_URL`, `SLACK_WEBHOOK_URL`, `HEALTHCHECK_PING_URL`
   (leave a value empty if you do not use it). `DATABASE_URL` is wired automatically to the internal connection string.
3. Apply. Render creates the database, builds the image and starts the worker.
4. Worker logs show: schema upgrade, then "initial load starting". The first load takes hours. When it ends the worker writes
   `data/.initial_load_done`, deletes the raw SIRENE files and starts the scheduler. Later restarts skip the load.
5. Optional paid sources: add `PAPPERS_API_KEY`, `GOOGLE_PLACES_API_KEY`, `INPI_USERNAME`, `INPI_PASSWORD` in the worker's
   Environment tab. Steps without keys are skipped and logged.

If the first load fails the worker sends an alert, waits one hour and exits; Render restarts it and the load resumes
(downloads are skipped when present, loads are upserts).

## 4. Getting the HubSpot CSV files
Each weekly run writes `data/exports/<date>/` (CSV parts plus `hubspot_errors_<date>.csv`) on the worker disk and keeps 8 weeks.
Use the worker's **SSH** tab (Render Dashboard, worker, Connect). Then copy the folder to your computer with `scp` using the
SSH address Render shows. Check Render's SSH docs for the exact command for your account. Uploading the two reference CSVs
works the same way, into `/app/data/raw/`. Import into HubSpot as described in `docs/07-hubspot-mapping.md`.
(Pushing exports to object storage would remove this manual step; it is not built.)

## 5. Operating rules
- `autoDeployTrigger` is `off`: deploy with Manual Deploy, never on Monday around 03:00 UTC. A deploy stops the running job
  (SIGTERM, up to 300 s grace). A job stopped that way is recorded as failed/stale by the daily check and the next Monday run repeats it.
- Backups: the weekly run writes `data/backups/fbdb_YYYY-MM-DD.dump` on the persistent disk (`scripts/snapshot.sh`, 28 days kept, G10 in docs/19); also use the database backups of the Render Postgres plan (check what your plan includes) and copy the folder off the server. `scripts/backup.sh` is for the VPS setup.
- Logs: Render log stream for the worker, plus `/app/data/reports/run_<date>.log` (kept 90 days) and the `job_runs` table.
- Disk is single-instance and cannot be shrunk. Resize in the dashboard before it fills; check `df -h /app/data` over SSH.

## 6. Troubleshooting
| Symptom | Check |
|---|---|
| Blueprint rejected | `render blueprints validate` (Render CLI); plan names change over time |
| `DATABASE_URL is not set` | database and worker must be in the same region and the Blueprint applied together |
| "missing reference CSVs" in the initial load log | set `NAF_CSV_URL` / `LEGAL_FORMS_CSV_URL` or upload the files to `/app/data/raw/`, then redeploy |
| Permission denied under `/app/data` | worker restart re-applies ownership (`render_entry.sh`) |
| Step FAIL exit 124 | step timeout (`STEP_TIMEOUT_SECONDS`), source API slow or down |
| Initial load very slow or out of memory | raise the worker plan and the database plan for the first load, then lower them |

## 7. Acceptance on Render (in order)
1. Blueprint applies; worker status "Live"; log shows "Schema ready."
2. Initial load completes; `data/.initial_load_done` exists; companies count plausible (as in `docs/15-deployment.md`, section 10, step 5).
3. Trigger a first weekly run once with `RUN_WEEKLY_ON_START=1` (set, redeploy, then remove it): exit 0, `job_runs` row `ok`, export files present.
4. Daily check prints `ok`; a test alert reaches Slack/email.
5. Download one export folder by SSH and import 100+ rows into a HubSpot sandbox (accents intact, no unintended merges).


## Loading only the target (scope: sectors and departments)
The full SIRENE stock is every active company of France (millions of rows), far above the 500k to 1M target. Set `SCOPE_NAF` (prefixes of the MAIN NAF, dots optional: `41,42,43,71.1`) and / or `SCOPE_DEPARTMENTS` (`69,42,01`, `2A`, `971`) on the worker before the first load; both are read by the initial load and by the weekly delta (`fbdb/load/scope.py`). Left empty = everything. CLI form: `python -m fbdb.load.sirene_loader --ul ... --etab ... --only-active --naf 43 --departments 69`.
- A company is loaded when its main NAF matches (a company without a valid NAF is not loaded when a NAF scope is set) and, when departments are set, when at least one of its establishments is in them; all its establishments in those departments are kept.
- The weekly delta adds a new company only if it is in scope; a company already in the base keeps being updated and closed whatever its new NAF.
- Widening the scope later: change the variables and run the load again (upsert, nothing is deleted); narrowing it does not delete rows already loaded.
- The batch note of `import_export_batches` records the scope used. Nothing is invented: only which rows of the official files are kept. The client still has to choose the sectors and departments (docs/16, question 1).
