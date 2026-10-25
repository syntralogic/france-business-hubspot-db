# Operations: GitHub Actions workflows

All are manual ("Run workflow"). They need the repository secret `DATABASE_URL`; the paid one also needs `PAPPERS_API_KEY`.
Do not run two workflows that change the schema at the same time (the "Schema" step waits for a lock).

| Workflow | What it does | Cost |
|---|---|---|
| Load real SIRENE data | Downloads the SIRENE stock files and loads the chosen NAF prefixes and departments. `purge_demo = no` keeps existing data. | free |
| Enrich (free sources) | BAN geocoding, ADEME RGE (full list), BODACC, register events, directors | free |
| Enrich directors (4 parallel workers) | Directors from the Annuaire (also stores its free revenue/net result). Resumable. | free |
| Enrich legal events (BODACC) | BODACC announcements and register events | free |
| Load full RGE (ADEME) | RGE only (157k rows from the official API) | free |
| Enrich Pappers (financials) | Pappers: financials, legal sheet, document list. One credit per company; start with 5. | **paid** |
| Coverage report / Diagnose stats | Read-only: how many companies have each kind of data, query timings. Result is written to the `diag` branch. | free |

Secrets are never written to the repo or to the `diag` branch.
