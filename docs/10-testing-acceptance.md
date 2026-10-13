# 14. Testing and Acceptance Criteria

## Automated tests (`pytest`, currently 71 passing: 54 unit + 17 PostgreSQL integration)
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
