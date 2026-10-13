# 7. Field-Level Data Dictionary

Legend: **R** = required, **O** = optional. Status vocabulary for contact-like fields:
`verified` (checked recently by an authoritative method), `sourced` (taken from a named source, not independently checked),
`inferred` (derived, never presented as fact), `unavailable` (no data).

## Legal company vs establishment
- **Company (legal unit)** = identified by **SIREN** (9 digits). Holds legal name, legal form, creation date, main NAF, directors, financials.
- **Establishment** = identified by **SIRET** (SIREN + 5-digit NIC = 14 digits). Holds physical address, local NAF, local phone/website, reviews.
- One company has 1..n establishments; exactly one is the head office (siège) while active.
- A **sole proprietor** (entrepreneur individuel) is a company whose legal identity is a natural person: `is_sole_proprietor = true`; name fields are personal data and subject to GDPR handling and non-diffusion.
- Records without SIREN (e.g., from a directory source that has not been matched) stay in `company_source_records` with `match_status = 'review'` and never enter `companies` until matched.

## companies
| Column | Type | R/O | Description | Example | Validation |
|---|---|---|---|---|---|
| company_id | BIGSERIAL | R | Internal unique ID | 1048213 | PK |
| siren | CHAR(9) | R* | Legal unit ID (*R for registry-sourced) | 552100554 | 9 digits + Luhn check |
| legal_name | TEXT | R | Official name | BOULANGERIE DUPONT SARL | non-empty, trimmed |
| trading_name | TEXT | O | Commercial name / enseigne | Au Fournil de Paul | |
| legal_form_code | TEXT | O | INSEE legal category code | 5499 | in INSEE nomenclature |
| legal_form_label | TEXT | O | Label | SARL | |
| main_naf | TEXT | O | NAF rev.2 code | 10.71C | regex `^\d{2}\.\d{2}[A-Z]$` |
| industry_label | TEXT | O | NAF label | Boulangerie et boulangerie-patisserie | |
| category | TEXT | O | artisan / commercant / liberal / other | artisan | enum |
| is_sole_proprietor | BOOL | R | Natural person business | false | |
| is_artisan | BOOL | O | From SIRENE artisan flag (RM) | true | |
| employee_range | TEXT | O | INSEE headcount band | 3 a 5 | code from INSEE list |
| size_category | TEXT | O | TPE/PME/ETI/GE | TPE | enum |
| status | TEXT | R | active/closed/dissolved/other | active | enum |
| creation_date | DATE | O | Legal creation | 2012-03-01 | <= today |
| closure_date | DATE | O | Cessation | 2024-06-30 | >= creation_date |
| is_diffusible | BOOL | R | False = must not be exported | true | |
| confidence_score | NUMERIC(4,3) | O | 0-1 record reliability | 0.912 | 0..1 |
| dup_status | TEXT | R | unique / merged / review | unique | enum |
| enrichment_status | TEXT | R | none / partial / complete | partial | enum |
| freshness_status | TEXT | R | fresh (<30d) / stale / expired | fresh | computed |
| missing_fields | TEXT[] | O | Names of empty key fields | {phone,email} | computed |
| created_at / updated_at / last_verified_at | TIMESTAMPTZ | R/R/O | Lifecycle timestamps | 2026-10-13T06:00Z | |
| directors_checked_at | TIMESTAMPTZ | O | Last directors lookup (Annuaire); NULL = never checked. Stops companies with no director being re-queried weekly | 2026-10-13T06:00Z | set by `fbdb.enrich.directors` |

Indexes: PK, UNIQUE(siren), (status), (main_naf), GIN trigram on legal_name.

## company_addresses (establishments)
| Column | Type | R/O | Description | Example | Validation |
|---|---|---|---|---|---|
| establishment_id | BIGSERIAL | R | Internal ID | 2210456 | PK |
| company_id | BIGINT | R | FK companies | 1048213 | exists |
| siret | CHAR(14) | R* | Establishment ID | 55210055400013 | 14 digits, Luhn (La Poste exception), starts with siren |
| nic | CHAR(5) | O | Last 5 digits of SIRET | 00013 | |
| is_head_office | BOOL | R | Siege flag | true | max 1 active per company |
| establishment_name | TEXT | O | Local trade name | Boulangerie Dupont Lyon 3 | |
| naf | TEXT | O | Local NAF | 10.71C | regex |
| status | TEXT | R | active/closed/other | active | enum |
| street, complement | TEXT | O | Address lines | 12 RUE DE LA PAIX | |
| postal_code | CHAR(5) | O | French postal code | 69003 | 5 chars, Corsica 2A/2B |
| city | TEXT | O | Commune | LYON 3EME | |
| department_code / department_name | TEXT | O | Department | 69 / Rhone | derived from postal code/INSEE commune |
| region | TEXT | O | Region | Auvergne-Rhone-Alpes | |
| country | TEXT | R | Default France | France | |
| latitude / longitude | NUMERIC(9,6) | O | WGS84 | 45.7578 / 4.8320 | lat 41..52, lon -6..10 (metropolitan) |
| standardized_address | TEXT | O | BAN-normalized single line | 12 Rue de la Paix 69003 Lyon | |
| address_status | TEXT | R | verified/sourced/unverified/invalid | verified | verified = BAN match score >= 0.8 |
| address_hash | CHAR(40) | O | SHA1 of normalized address | | for dedup |

Indexes: UNIQUE(siret), (company_id), (postal_code), (department_code), (address_hash), partial unique head office.

## company_contacts
| Column | Type | R/O | Description | Example | Validation |
|---|---|---|---|---|---|
| contact_type | TEXT | R | phone/email/website/linkedin/social | phone | enum |
| value | TEXT | R | As received | 04 72 00 00 01 | |
| value_normalized | TEXT | R | E.164 / lowercase email / bare domain | +33472000001 | phone via libphonenumber, FR region |
| is_primary | BOOL | R | Main contact of its type | true | one primary per (company, type) |
| value_status | TEXT | R | verified/sourced/inferred/unavailable | sourced | enum |
| source_id | SMALLINT | R | FK data_sources | 7 | |
| collected_at / last_verified_at | TIMESTAMPTZ | R/O | | | |

Email rules: syntax check + MX lookup; role addresses (contact@, info@) allowed; **no guessing of addresses** (`inferred` values are never exported as email).

## company_directors
| Column | Type | R/O | Description | Example | Validation |
|---|---|---|---|---|---|
| first_name, last_name | TEXT | O | Legal representative | Marie / DUPONT | Unicode normalized, title case for display |
| role | TEXT | O | Gerant, President, DG... | Gerant | |
| birth_year | SMALLINT | O | Year only (minimization) | 1978 | 1900..current-16 |
| is_active, start_date, end_date | | O | Tenure | | end >= start |
| source_id, last_verified_at | | R/O | | | |

Only roles tied to the company's public registration; no home addresses, no full birth dates.

## company_financials
| Column | Type | R/O | Description | Example | Validation |
|---|---|---|---|---|---|
| fiscal_year | SMALLINT | R | Financial year | 2024 | 1990..2100 |
| revenue_eur | NUMERIC(16,2) | O | Turnover | 412000.00 | >= 0 |
| net_result_eur | NUMERIC(16,2) | O | Net result | 18500.00 | |
| employees | INTEGER | O | Headcount at year end | 4 | >= 0 |
Unique (company_id, fiscal_year, source_id). Many companies do not publish accounts: null is expected.

## company_certifications
cert_type (RGE, Qualibat...), cert_label, valid_from, valid_to, source_id, collected_at. Unique (company_id, cert_type, cert_label, valid_from). Expired certifications are kept but flagged by `valid_to < today`.

## company_reviews
provider, provider_place_id, rating (0-5), review_count, profile_url, match_confidence (0-1), collected_at. Only stored when the provider terms allow; match_confidence < 0.8 means the match is not exported.

## data_sources, company_source_records, company_change_history, import_export_batches, data_quality_issues
See `sql/001_schema.sql` for full column lists. Key rules:
- `company_source_records`: UNIQUE (source_id, external_key, payload_hash): the same payload is never stored twice.
- `company_change_history`: one row per changed field with old/new value, batch and source.
- `import_export_batches`: counters (added, updated, closed, unchanged, review) feed the weekly report.
- `data_quality_issues`: issue_code, severity, status (open/resolved/ignored).

## Field availability summary
| Field | Source | Reliability | Legal note |
|---|---|---|---|
| SIREN/SIRET, name, NAF, legal form, status, dates | SIRENE | High | Open licence, exclude non-diffusible |
| Address, coordinates | SIRENE + BAN geocoder | High / Medium | Open |
| Phone, email, website | Licensed provider only | Medium | Contract + GDPR/CNIL rules |
| Directors | INPI RNE / Annuaire / Pappers | High | Personal data: minimize |
| Revenue, financial year | Filed accounts via INPI / Pappers | Medium (partial) | Not all accounts are public |
| Employee range | SIRENE | Medium (band, can be outdated) | Open |
| Certifications | ADEME RGE etc. | High where open | Open for RGE |
| Google rating/reviews | Google Places | Medium | ToS restricts storage |
