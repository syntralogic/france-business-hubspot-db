# 14. Testing and Acceptance Criteria

## Automated tests (`pytest`, currently 109 passing: 83 unit + 26 PostgreSQL integration)
| Area | Covered |
|---|---|
| Normalization | SIREN/SIRET Luhn (incl. La Poste rule), names, addresses, phones, emails, domains |
| Dedup/resolve | SIRET/SIREN ladder, fuzzy with phone, shared address, same name far apart, review tier, conflict policies |
| SIRENE mapping | active/closed, sole proprietor, non-diffusible, artisan flag, Corsica/DOM departments, CSV streaming |
| API flattening | current period selection, JSON booleans, reuse of bulk mappers |
| HubSpot export | rejection/warning codes, duplicate SIRET, shared domain, UTF-8 no BOM, control chars, file split constant |
| Directors enrichment | Annuaire response mapping (natural persons only, birth year only, name normalization); PostgreSQL: scope guards, insert/remove/reactivate, old/new history, idempotent re-run, API failure -> partial batch and retry |
| Automated (needs TEST_DATABASE_URL) | `tests/integration`: schema, bulk upsert, weekly delta, change history, closures, idempotent re-run, export view, run against PostgreSQL 16 on 13 Oct 2026 |

## Acceptance criteria (measured on the delivered dataset)
1. 100% of exported rows have a valid SIRET and SIREN (Luhn) and SIRET starts with SIREN.
2. 0 duplicate SIRET in any export file; 0 non-diffusible units exported.
3. Dedup: precision >= 99% on a hand-labelled sample of >= 500 pairs (recall reported).
4. Registry fields (name, NAF, status, address) match SIRENE on a random 1,000-row audit at >= 99%.
5. Phone/email: fill rate **reported** per source; invalid format rate 0% in export (invalid values are blanked).
6. A test import of 100+ rows in HubSpot creates the expected records, no unintended merges, correct accents.
7. Weekly run is idempotent (second run of the same week produces 0 new history rows) and finishes under the agreed window.
8. Weekly report lists added / updated / closed / unchanged / review counts, reconciling to the batch table.
9. Setup docs allow a fresh machine to reproduce a load from scratch.

## Live smoke test (run once with real network and the INSEE key)
`PYTHONPATH=src python scripts/live_smoke.py` checks, without writing to the database: one SIREN on the INSEE API, a pull of 100 establishments flattened with the production mapper, and the Annuaire client on two known SIRENs plus one unknown. It prints PASS/FAIL per check and exits 1 on any failure. Record the result in `docs/13-verification-log.md`. Not yet run against the live APIs (no INSEE key and no outbound access in the build environment).

## Full-SIRENE load test
`scripts/load_test.sh [WORKDIR]` on a server (throw-away database, ~25 GB free): downloads the two stock files, loads a 100k-row sample, then the full active set, runs the weekly delta twice (second run must add 0 history rows) and the export, logging duration and peak memory per step to `reports/load_test_<date>.log`. Not yet run (needs INSEE key, internet and PostgreSQL).

## Dedup precision/recall on a labelled sample (criterion 3)
`python -m fbdb.report.dedup_eval sample pairs.csv --out to_label.csv --n 500` writes a stratified labelling sheet with the pipeline's
prediction and an empty `label` column. A human fills `same` or `different`; then `python -m fbdb.report.dedup_eval score to_label.csv`
prints the confusion matrix, precision of automatic matches, recall, and exits 1 unless precision >= 99% on >= 500 labelled pairs.
Candidate pairs come from the real data (for example records sharing a name, postal code or phone). Labelling needs a person; the tool does not label.

## Acceptance metrics script (Chunk 7)
`python -m fbdb.report.metrics` measures on the export view: coverage (exportable / active establishments, and vs the volume target),
fill rate per export column, and match rates (geocoding, directors, RGE). Targets are proposed defaults in `DEFAULT_TARGETS`
(registry-grade fields gated; phone, email, revenue, directors, ratings report-only, as agreed in the brief). Override with `--targets file.json`.
Outputs: `metrics.json`, `fill_rates.csv`, `client_report.md` in `reports/metrics/`. Run it on the real dataset after the full SIRENE load.
