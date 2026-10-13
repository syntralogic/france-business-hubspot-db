# Appendix A. Verification log (13 Oct 2026)

The brief asks that source and HubSpot capabilities are verified before the mapping is finalised. This is what was checked, where, and what is still open. Vendor terms change: re-check before go-live.

## Verified
| Topic | Finding | Source |
|---|---|---|
| HubSpot import limits | Imports API: up to 80,000,000 rows/day; each file at most 1,048,576 rows or 512 MB. Our exporter splits below the row limit. | developers.hubspot.com/docs/api/crm/imports |
| HubSpot unique identifiers (companies) | Record ID, Company domain name, or a **custom property that requires unique values** can identify companies in an import; Record ID overrides the others. With a custom unique property, Company domain name stops requiring unique values. | knowledge.hubspot.com "Import records for a single object" (22 Jul 2026), "Understand the import tool" (6 Mar 2026) |
| INSEE API Sirene | Version 3.11, free account + key on portail-api.insee.fr, header `X-INSEE-Api-Key-Integration`, quota 30 requests/min, cursor pagination, 1,000 results per page. Data under Licence Ouverte 2.0. | insee.fr (Sirene actualités, Jun 2026), api.gouv.fr "Quelle API Sirene utiliser ?", portal wrappers |
| Annuaire / Recherche d'entreprises API | Searches by name/address, does not expose the full Sirene base; use for per-SIREN enrichment only | api.gouv.fr "Quelle API Sirene utiliser ?" |
| Annuaire directors response shape | `GET /search?q=<siren>&per_page=1` returns `results[].dirigeants`: natural persons carry `nom, prenoms, annee_de_naissance, date_de_naissance, qualite, type_dirigeant`; legal entities carry `siren, denomination, qualite`. The mapper keeps natural persons and the birth year only. Verified by reading a live response through the documentation tooling; **not** exercised from the development sandbox, whose egress proxy blocks this host. Run `python -m fbdb.enrich.directors --limit 20` once on the real server and check the batch row before relying on it. | recherche-entreprises.api.gouv.fr (13 Oct 2026) |
| Google Places storage | Only `place_id` may be cached indefinitely; Places lat/lng up to 30 consecutive days; ratings and reviews have no caching exception | cloud.google.com Maps Platform service-specific terms (sections 5.4) |
| B2B prospecting by email | Opt-out regime in B2B subject to information, relevance to the profession and a simple opposition; B2C needs opt-in | CCI Paris IDF fiche (25 Jun 2026), CNIL commentary (Jun 2026) |

## Not verified (must be confirmed before contracting)
- Pappers API: current credit prices and whether the contract allows long-term storage and loading into a CRM. Pricing page: pappers.fr/api. No price is quoted in this repository on purpose.
- Pages Jaunes / Solocal: whether a data licence is offered, its price and CRM-storage rights. Scraping stays out of scope.
- The client's HubSpot subscription: daily import row limit, ability to create a unique custom property on Companies, parent/child associations, date format.
- Licensed B2B contact providers: sourcing compliance and match rate on a 1,000-record sample.

- Annuaire des Entreprises API rate limit: the response carries no limit information. The client spaces calls 0.25 s apart (`ANNUAIRE_MIN_INTERVAL_S`); confirm the published limit on api.gouv.fr before raising throughput.

## Changes made in the code because of this verification
- `SireneApiClient` now spaces calls 2.1 s apart (quota 30/min) instead of relying on 429 retries.
- `run_export --siret-is-import-key` keeps the shared domain on every establishment when the import uses the unique SIRET property.
- Google rating and review count columns stay empty unless a licensed source supplies them.
- `sql/000_reference_data.sql`: department/region names come from the official geo.api.gouv.fr list (101 departments, 18 regions, read 13 Oct 2026 through the documentation tooling). Employee-range labels follow the INSEE trancheEffectifs nomenclature. The legal-form table is a curated subset written by hand: an unknown code is exported as the raw code, and `fbdb.load.reference` loads the complete official table.
- NAF labels are not typed in: `fbdb.load.reference --naf` loads the INSEE file (the sandbox could not download it).
- Closure date: the INSEE variable names for the period start / state-change flag could not be re-checked against the official variable list (robots/egress). The mapping only fires when both fields are present, so a wrong or missing name leaves the date unknown and the weekly run records the date it first saw the closure (`closure_date_basis = 'observed'`). Check one real closed establishment from the API before relying on `insee` dates.
- `fbdb.enrich.directors` keeps natural persons only, stores the birth year only, and skips sole proprietors, non-diffusible and closed companies.
