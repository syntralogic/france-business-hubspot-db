# France Business & Artisan Database for HubSpot CRM

Pipeline that builds a deduplicated, enriched database of French businesses and artisans
(SIRENE-based) and exports a HubSpot-ready CSV. No website or frontend: data in, CSV out.

## Status
Built incrementally, one commit per step. Docs in `docs/` (01-12 follow the requested response order).
Implemented and unit-tested (40 tests): normalization, dedup, conflict resolution, SIRENE bulk + API delta mapping,
HubSpot export with validation. SQL is integration-tested against a real PostgreSQL 16 (tests/integration, 4 tests: bulk load, weekly delta, change history, closures, export). The live INSEE API path still needs a real INSEE API key (see docs/10-testing-acceptance.md). Source and HubSpot facts were re-verified on 13 Oct 2026: see docs/13-verification-log.md.

## Quick start
```
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # fill DATABASE_URL and INSEE_API_KEY
psql $DATABASE_URL -f sql/001_schema.sql -f sql/002_staging.sql -f sql/003_pipeline_state.sql -f sql/004_export_view.sql
python -m fbdb.load.sirene_loader --ul StockUniteLegale_utf8.csv --etab StockEtablissement_utf8.csv --only-active
python -m fbdb.load.weekly            # weekly delta (or scripts/run_weekly.sh from cron)
python -m fbdb.export.run_export      # writes HubSpot CSV + error report to data/exports/
pytest
TEST_DATABASE_URL=postgresql://user:pw@localhost/fbdb_test pytest tests/integration -q   # real PostgreSQL, throw-away DB (drops schema public)
```

## Planned stack
- PostgreSQL (central store), Python 3.11+ (ETL, matching, enrichment)
- Weekly scheduled run (cron or systemd timer)
- Primary source: INSEE SIRENE (bulk files + API); enrichment from authorized providers only

## Layout
- docs/ : blueprint documents (requirements, sources, schema, mapping, costs)
- sql/ : PostgreSQL DDL
- src/fbdb/ : extract, transform, match, enrich, load, export
- config/ : settings templates (no secrets)
- tests/ : unit and integration tests
- data/ and reports/ : local outputs (git-ignored)

## Principles
- Never fabricate contact data; every field carries source, date and status.
- SIREN = legal company, SIRET = establishment.
- Secrets only via environment variables (.env, never committed).
