# Operations: GitHub Actions workflows

All are manual ("Run workflow"). They need the repository secret `DATABASE_URL`; the paid one also needs `PAPPERS_API_KEY`.
Do not run two workflows that change the schema at the same time (the "Schema" step waits for a lock).

| Workflow | What it does | Cost |
|---|---|---|
| Load real SIRENE data | Downloads the SIRENE stock files and loads the chosen NAF prefixes and departments. `purge_demo = no` keeps existing data. | free |
| Enrich (free sources) | BAN geocoding, ADEME RGE (full list), BODACC, register events, directors | free |
| Enrich directors (4 parallel workers) | Directors from the Annuaire (also stores its free revenue/net result). Resumable; `rounds` = N starts up to N runs in a row until nobody is waiting. | free |
| Enrich legal events (BODACC) | BODACC announcements and register events | free |
| Load full RGE (ADEME) | RGE only (157k rows from the official API) | free |
| Enrich Pappers (financials) | Pappers: financials, legal sheet, document list. One credit per company; start with 5. | **paid** |
| Database maintenance | TRUNCATE the load staging tables and ANALYZE (frees ~40 MB). Also runs automatically at the end of every SIRENE load. | free |
| Coverage report / Diagnose stats | Read-only: how many companies have each kind of data, query timings. Result is written to the `diag` branch. | free |

Secrets are never written to the repo or to the `diag` branch.

## Schedules (UTC, unattended)

| When | Workflow | Notes |
|---|---|---|
| Every Sunday 03:00 | Weekly enrichment (free sources) | geocode, RGE, directors (up to 10,000 companies), BODACC 8 days, ANALYZE |
| Every Monday 06:00 | Export HubSpot CSV | fresh CSV as an artifact (kept 14 days) |
| 3rd of each month 02:00 | Load real SIRENE data | new SIRENE stock for NAF 41/42/43 and the departments listed in the workflow file; existing data is kept |

All workflows that write to the database share one lock (`db-writes`): they queue instead of running at the same time. A failed
scheduled run turns red and GitHub emails the repository owner. To change the departments of the monthly refresh, edit
`SCOPE_DEPARTMENTS` in `.github/workflows/load-real-data.yml`.

## Paid contact steps (Firecrawl / Hunter / Twilio) without a shell
Workflow **Enrich contacts**: pick ONE step (1 website + phones / emails / people from the proven site, 2 directors -> interlocutors, 3 Hunter email finder, 4 Hunter verifier, 5 Twilio line type, 6 Lusha direct mobile of a director, needs a signed contract) and a `limit`. Always start with `limit` 5 and read the log.
1. Add the keys as repository secrets (Settings > Secrets and variables > Actions): `FIRECRAWL_API_KEY`, `HUNTER_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `LUSHA_API_KEY`, `LUSHA_CONTRACT_REF` (only what the step needs; `DATABASE_URL` is already there).
2. Suggested order for the client's test lot (about 100 companies): free directors workflow, then step 2, step 1, step 3, step 4, step 5.
3. The run prints counts only (run summary); phones, emails and names are never written to the repo or to a branch. Look at the values in the database itself.
4. Daily caps are set in the workflow file (`FIRECRAWL_DAILY_CAP`, `HUNTER_FINDER_DAILY_CAP`, `HUNTER_DAILY_CAP`, `TWILIO_DAILY_CAP`); edit them to change the budget.
Step 6 refuses to run without `LUSHA_CONTRACT_REF` (the reference of the signed contract). None of these steps has run against a live key yet.
