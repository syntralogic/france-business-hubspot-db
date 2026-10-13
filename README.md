# France Business & Artisan Database for HubSpot CRM

Pipeline that builds a deduplicated, enriched database of French businesses and artisans
(SIRENE-based) and exports a HubSpot-ready CSV. No website or frontend: data in, CSV out.

## Status
Built incrementally, one commit per step. Start with `docs/00-index.md`: it maps the 16 sections of the client brief to files.
Implemented and tested (52 tests: 44 unit + 8 against a real PostgreSQL 16): normalization, dedup, conflict resolution, SIRENE bulk + API delta,
directors enrichment (Annuaire des Entreprises), HubSpot export with validation. Not built yet: licensed phone/email, Pappers financials, ADEME certifications, Google ratings (see docs/11-timeline-cost.md). The live INSEE API path needs a real INSEE API key, and the Annuaire client should be run once against the live API on the server (see docs/10-testing-acceptance.md and docs/13-verification-log.md). Source and HubSpot facts were re-verified on 13 Oct 2026: see docs/13-verification-log.md.

## Quick start
```
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # fill DATABASE_URL and INSEE_API_KEY
psql $DATABASE_URL -f sql/000_reference_data.sql -f sql/001_schema.sql -f sql/002_staging.sql -f sql/003_pipeline_state.sql -f sql/004_export_view.sql -f sql/005_directors_enrichment.sql -f sql/006_closure_date.sql
python -m fbdb.load.sirene_loader --ul StockUniteLegale_utf8.csv --etab StockEtablissement_utf8.csv --only-active
python -m fbdb.load.weekly            # weekly delta (or scripts/run_weekly.sh from cron)
python -m fbdb.enrich.directors --limit 5000   # directors refresh (also run by run_weekly.sh)
python -m fbdb.export.run_export      # writes HubSpot CSV + error report to data/exports/
pytest
TEST_DATABASE_URL=postgresql://user:pw@localhost/fbdb_test pytest tests/integration -q   # real PostgreSQL, throw-away DB (drops schema public)
```

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
