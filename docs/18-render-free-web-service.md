# 18. Free Render web service wrapper (Appendix F)

Alternative to the paid Blueprint in `docs/17-render-deployment.md` (Postgres basic-4gb + worker + disk). Render's free tier
offers web services only (cron jobs and workers are paid), so `src/fbdb/web.py`, a standard-library HTTP server, lets the
weekly pipeline run inside a free web service. It is meant for a demo or a small data set, not for the full SIRENE load.

## Endpoints
| Endpoint | Auth | What it does |
|---|---|---|
| `GET /health` | none | `ok`. Render health check and keep-alive target. |
| `POST /run?job=all` | `X-Run-Token` header | Starts `scripts/run_weekly.sh` in the background (202): the full weekly run, `job_runs` record and failure alert included. `job=weekly` or `job=directors` runs just that step. 409 if a job is already running. |
| `GET /status` | `X-Run-Token` header | State (`idle`, `running`, `success`, `failed`) and the log tail of the last run. |

Without `RUN_TOKEN` the `/run` and `/status` endpoints answer 503: jobs never start without a secret. The `key` query
parameter is accepted too, but the header is preferred (URLs end up in logs).

## Render settings
- New > Web Service > this repo, branch `main`, region Frankfurt, instance type **Free**.
- Build command: `pip install -r requirements.txt`
- Start command: `PYTHONPATH=src python -m fbdb.web`
- Health check path: `/health`
- Environment: `DATABASE_URL` (Supabase *Session pooler* string), `INSEE_API_KEY`, `RUN_TOKEN` (long random string),
  `PYTHON_VERSION=3.12.3`, `EXPORT_AFTER_WEEKLY=0`
- Apply the schema once from your own computer: `DATABASE_URL=... scripts/init_db.sh` (Render does not do it here).

`EXPORT_AFTER_WEEKLY=0` because the free instance has no persistent disk: an export written there disappears. Run
`python -m fbdb.export.run_export --out <folder>` from your computer against the same database instead.

## Scheduling (a sleeping free service cannot wake itself)
Call the service once a week from an external scheduler (cron-job.org, or a GitHub Actions `schedule` workflow):
`curl -X POST -H "X-Run-Token: $RUN_TOKEN" "https://<service>.onrender.com/run?job=all"`
The first call after idle takes about a minute (cold start): use a generous timeout. While a job runs, the server pings
its own `RENDER_EXTERNAL_URL/health` every 5 minutes so Render does not put it to sleep mid-run.

## Limits to know
- Free instance: 512 MB RAM, sleeps after 15 min without inbound traffic, no persistent disk. Only the last run's status and
  log tail are kept in memory (lost on restart); the durable record is the `job_runs` table.
- A restart or redeploy kills a running job. The pipeline is idempotent: run it again.
- Supabase free: 500 MB database (the full SIRENE stock does not fit), project paused after 7 days of inactivity.
  A free Render database is deleted after 30 days.
- Not run on a real Render instance from this repository's CI: check `/status` after the first run.
