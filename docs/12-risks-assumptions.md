# 16. Key Risks, Assumptions and Unresolved Questions

## Risks
| Risk | Impact | Mitigation |
|---|---|---|
| No free phone/email source | Low contact coverage | Budget a licensed provider; report fill rate per field; never fabricate |
| Pages Jaunes scraping prohibited | Legal/contract exposure | Only a written data licence |
| Google ToS limits storage | Ratings cannot be bulk-stored | Optional, on-demand, refreshable only |
| Personal data (directors, sole proprietors) | GDPR/CNIL exposure | Minimization, suppression list, counsel review |
| HubSpot domain-based dedupe | Establishments merge | Domain only on first row; test import |
| HubSpot plan limits (rows/day, unique props, associations) | Import plan changes | Confirm in Phase 1; split files; weekly deltas |
| SIRENE API/field changes | Pipeline breaks | Version pinned (3.11), contract tests, monitoring |
| Fuzzy dedup errors | Wrong merges | Identifier-first ladder, review tier, labelled-sample tuning |
| Untested SQL on real data | Late defects | PostgreSQL integration tests in Phase 2/6 |

## Assumptions (and impact)
1. Metropolitan France + DOM; changing scope changes volume.
2. Active establishments only in the main deliverable; closed ones kept for change tracking.
3. One HubSpot company per SIRET; if the client prefers one per SIREN, export changes to head-office-only rows.
4. Client can create custom HubSpot properties; otherwise fields collapse into a notes property.
5. Weekly import into HubSpot is a manual file import unless the client's plan/API allows automation.

## Unresolved questions for the client
1. Which sectors/departments/size filters define the 500k-1M target?
2. Include sole proprietors (EI/micro-entrepreneurs)? They carry the highest privacy sensitivity.
3. HubSpot subscription tier and existing company data (needed for dedupe against current records)?
4. Budget and preference for phone/email data providers (existing licences)?
5. Who is the data controller, and is there a DPO to validate legal basis and retention?
6. Automated HubSpot import via API, or manual CSV import each week?
