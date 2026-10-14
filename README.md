# France Business & Artisan Database for HubSpot CRM

Pipeline that builds a deduplicated, enriched database of French businesses and artisans
(SIRENE-based) and exports a HubSpot-ready CSV. No website or frontend: data in, CSV out.

## Status
Built incrementally, one commit per step. Start with `docs/00-index.md`: it maps the 16 sections of the client brief to files.
Implemented and tested (109 tests: 83 unit + 26 against a real PostgreSQL 16, run 14 Oct 2026): normalization, dedup, conflict resolution, SIRENE bulk + API delta,
directors enrichment (Annuaire des Entreprises), licensed phone/email file loader (needs a signed provider contract, see docs/14-licensed-contacts.md), HubSpot export with validation. Geocoding, ADEME RGE, BODACC and INPI RNE (Chunk 2 complete) built. Acceptance metrics script built (Chunk 7). Pappers financials built (paid key needed, daily credit cap). Google Places ratings built (paid key, daily call cap, export keeps ratings out unless licensed). Chunk 6 part 1 built: run_weekly.sh with per-step log, job_runs record, Slack/email failure alerts, missed-run check (`python -m fbdb.ops.jobs check --job weekly`, run daily from cron). Chunk 6 part 2 built: Dockerfile, docker-compose.yml, deploy/crontab.example and docs/15-deployment.md (written, not yet run on a real server). (see docs/11-timeline-cost.md). The live INSEE API path needs a real INSEE API key, and the Annuaire client should be run once against the live API on the server (see docs/10-testing-acceptance.md and docs/13-verification-log.md). Dedup evaluation tool built (labelling sheet + precision/recall gate). Open client decisions and provider checklist: docs/16-client-questions.md. Source and HubSpot facts were re-verified on 13 Oct 2026: see docs/13-verification-log.md.

## Quick start
```
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # fill DATABASE_URL and INSEE_API_KEY
scripts/init_db.sh               # creates (or upgrades) the schema: sql/000..011 in order
python -m fbdb.load.reference --naf naf_rev2.csv --legal-forms categories_juridiques.csv   # INSEE CSVs: NAF labels, full legal form list
python -m fbdb.load.sirene_loader --ul StockUniteLegale_utf8.csv --etab StockEtablissement_utf8.csv --only-active
python -m fbdb.load.weekly            # weekly delta (or scripts/run_weekly.sh from cron)
python -m fbdb.enrich.directors --limit 5000   # directors refresh (also run by run_weekly.sh)
python -m fbdb.enrich.geocode --limit 50000     # BAN/IGN batch geocoding (threshold 0.6), also run by run_weekly.sh
python -m fbdb.enrich.ademe_rge RGE.csv        # ADEME RGE certificates by exact SIRET (CSV from data.ademe.fr, liste-des-entreprises-rge-2)
python -m fbdb.enrich.bodacc --days 8             # BODACC collective procedures / radiations -> bodacc_events + review flags (also in run_weekly.sh)
python -m fbdb.enrich.inpi --limit 500             # INPI RNE cross-check (radiation / director mismatch flags only); needs INPI_USERNAME/INPI_PASSWORD, skipped in run_weekly.sh if unset
python -m fbdb.enrich.licensed_contacts FEED.csv --contract-ref REF   # licensed phone/email file, on delivery only
python -m fbdb.enrich.pappers --limit 200          # Pappers revenue/headcount, PAID, hard daily cap PAPPERS_DAILY_CAP (skipped in run_weekly.sh without PAPPERS_API_KEY)
python -m fbdb.enrich.places --limit 100            # Google Places rating/reviews, PAID, daily cap PLACES_DAILY_CAP, selection rule in the module docstring (skipped in run_weekly.sh without GOOGLE_PLACES_API_KEY)
python -m fbdb.report.metrics --out reports/metrics   # coverage, per-field fill rates, match rates, gaps vs targets, short client report (client_report.md)
python -m fbdb.report.dedup_eval sample pairs.csv --out to_label.csv   # dedup precision check: label pairs by hand, then `score` (see docs/10)
python -m fbdb.export.run_export      # writes HubSpot CSV + error report to data/exports/
pytest
TEST_DATABASE_URL=postgresql://user:pw@localhost/fbdb_test pytest tests/integration -q   # real PostgreSQL, throw-away DB (drops schema public)
```

## Deployment
See `docs/15-deployment.md` (full runbook: VPS sizing, Docker, first load, cron, HubSpot import, updates, backups, monitoring, acceptance checklist).
Short version on a server: `cp .env.example .env` (fill it), `docker compose up -d db`, `scripts/init_db.sh`, `scripts/initial_load.sh`, install
`deploy/crontab.example`. Later updates: `scripts/deploy.sh`. CI runs the tests and builds the image on every push.

Render (managed platform): `render.yaml` + `Dockerfile.render`, see `docs/17-render-deployment.md` (worker with persistent disk and its own scheduler; Render Cron Jobs cannot be used because they have no disk).

## Stack
- PostgreSQL (central store), Python 3.11+ (ETL, matching, enrichment)
- Weekly scheduled run (cron or systemd timer)
- Primary source: INSEE SIRENE (bulk files + API); enrichment from authorized providers only

## Layout
- docs/ : blueprint documents (index in docs/00-index.md)
- sql/ : PostgreSQL DDL
- src/fbdb/ : extract, transform, match, enrich, load, export
- examples/ : fictional sample CSV (never import)
- scripts/ : cron wrapper, sample and schema-doc generators
- tests/ : unit and integration tests
- data/ and reports/ : local outputs (git-ignored)

## Principles
- Never fabricate contact data; every field carries source, date and status.
- SIREN = legal company, SIRET = establishment.
- Secrets only via environment variables (.env, never committed).
