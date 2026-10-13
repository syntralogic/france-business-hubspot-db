# 15. Timeline and Realistic Cost Breakdown

All figures are **estimates** to be refined after Phase 1. Third-party prices are deliberately not quoted here: they must be obtained from providers.

## Phases (one senior data engineer)
| Phase | Scope | Effort (days) |
|---|---|---|
| 1 Requirements and source feasibility | Confirm scope/sectors, SIRENE + INSEE API key, test candidate providers on a 1,000-record sample, HubSpot plan check | 3-5 |
| 2 Schema and source integration | PostgreSQL schema, SIRENE bulk + API, INPI/Annuaire/BODACC/ADEME connectors | 8-12 |
| 3 Initial collection and normalization | Full SIRENE load, filtering, BAN geocoding, quality metrics | 5-8 |
| 4 Deduplication and enrichment | Matching with licensed sources, directors, financials, certifications, confidence calibration | 8-12 |
| 5 HubSpot export validation | Property setup, test import, mapping fixes, error reports | 4-6 |
| 6 Weekly automation | Scheduler, retries, monitoring, reports, PostgreSQL integration tests | 5-7 |
| 7 Testing, documentation, handover | Acceptance audit, runbooks, training | 5-7 |
| **Total** | | **38-57 days (about 8-12 weeks)** |

Already in this repository: schema, SIRENE loader, weekly delta, normalization, dedup/resolve, HubSpot export and validation, with tests. They are untested against a live PostgreSQL and real INSEE data until Phase 2/3.

## One-time costs (pipeline build)
Development = effort days x agreed day rate (e.g. 38-57 days). Add the one-time cost of any data purchase (initial licence fees or bulk enrichment credits).

## Recurring costs
| Item | Estimate / driver |
|---|---|
| Server 8 vCPU / 32 GB / 100 GB SSD | roughly EUR 60-150 per month (provider dependent) |
| SIRENE / INSEE APIs, BAN geocoder, BODACC, ADEME | free (INSEE quota applies) |
| Directors/financials (Pappers or similar) | per-credit or subscription: depends on volume refreshed weekly |
| Phone/email licence (Solocal-type or B2B provider) | per-contract: usually the largest cost |
| Google Places | per-request pricing; only for records where ratings add value |
| Maintenance and monitoring | about 0.5-1 day per month (API changes, failures, review queue) |

## Expected coverage (to be measured, not promised)
- SIRENE contains far more than 1M active establishments; a 500k-1M+ deliverable is a **selection** (sectors, departments, employer/artisan flags), not a source limit.
- Registry fields: near-complete for diffusible units. Directors: good for companies, none for most sole proprietors. Revenue: only where accounts are public. Phone/email: depends entirely on the licensed source and its match rate.
- Weekly processing: INSEE API quota is 30 requests/min and the client spaces calls 2.1 s apart; at 1,000 rows per page a 300,000-row weekly delta is about 300 calls, i.e. roughly 11 minutes of API time (arithmetic, to be measured). Delta load and diff in minutes to about an hour; monthly full reconciliation about 1-3 hours (to be measured).
