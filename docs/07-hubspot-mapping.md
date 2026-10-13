# 10. HubSpot Field Mapping and Import Guide

## What was verified (HubSpot Knowledge Base, import requirements)
- Header row must correspond to HubSpot properties; custom properties and dropdown options must exist **before** import.
- File limits: 512 MB and 1,048,576 rows per file. Daily row limits depend on subscription (documented as 500,000 or 10,000,000 rows/day; 80,000,000/day via the imports API). Our exporter splits files at 1,000,000 rows.
- Companies need at least **Name or Company domain name**.
- A blank cell does **not** clear an existing value in HubSpot.
- To update existing records, include a unique identifier. If **Record ID** is present it overrides all other identifiers.

## What must be confirmed in the client's portal (not assumed)
1. Subscription tier and daily import row limit.
2. ~~Whether a custom company property set to "unique value" can be the dedupe key for Companies~~ **Verified 13 Oct 2026** (HubSpot KB "Import records for a single object", updated 22 Jul 2026; "Understand the import tool", updated 6 Mar 2026): custom properties that require unique values are available as unique identifiers for **contacts, companies**, deals and others. Using one for companies means **Company domain name no longer requires unique values**. Still to confirm in the client's portal: that the property can be created with "require unique values" on their subscription, and the behaviour on the 100-row test import.
3. Whether parent/child company associations are available on the plan.
4. Date format accepted by the portal (we send ISO `YYYY-MM-DD`; set the import's date format accordingly).
5. A 100-row test import in a test/sandbox portal before the first full import.

## Modelling decision
**One HubSpot company = one active establishment (SIRET).** Prospecting is local (a baker in Lyon and the same firm's shop in Villeurbanne are different targets). Grouping by `SIREN`, flag `Siege` marks the head office. Optional: link establishments to the head office with parent/child association if the plan supports it (post-import step by SIREN).

**Domain handling.** By default HubSpot treats **Company domain name** as the dedupe key, so establishments of one company sharing a website would merge. Two modes:
- *Default (conservative):* the exporter keeps the domain only on the first row and blanks it (`DOMAIN_SHARED` warning) on the others; the website stays in `Website URL`.
- *SIRET as import key* (`python -m fbdb.export.run_export --siret-is-import-key`): use only when the import maps the unique custom property `SIRET` as the identifier. Per HubSpot's documentation the domain then stops requiring unique values, so every establishment keeps its domain. Switch on only after the 100-row test import confirms it.

## Avoiding duplicates on repeated imports
1. Primary key = `SIRET` (custom text property, "require unique values" enabled) and `ID interne`.
2. If the unique-property dedupe is **not** supported for Companies on the plan: after the first import, export companies from HubSpot (`Record ID`, `SIRET`), store the Record ID in our DB, and include `Record ID` in weekly update files (Record ID overrides all other identifiers).
3. Weekly files: send only new + changed + closed records (diff from `company_change_history`), not the full 1M each week.
4. Closed establishments are exported with `Statut etablissement = closed` (`--include-closed`) so HubSpot is updated rather than left stale.

## Contacts (directors)
- Director name/role are stored as company-level properties (`Dirigeant ...`): no contact record is created without a deduplicable key.
- Import a **Contacts** file only for people with a sourced professional email: Email = unique identifier, associated to the company by a shared key (confirm the association column in the test import). Directors are personal data: see privacy section. Sole proprietors' details need extra caution.

## Encoding
UTF-8, no BOM, comma separated, double-quote escaping (Python `csv`). French accents preserved (covered by a unit test). If opening in Excel, use Data > From Text/CSV with UTF-8 instead of double-clicking.

## Mapping: standard HubSpot company properties
| CSV header | HubSpot internal name | Source (internal) |
|---|---|---|
| Company name | `name` | establishment_name / trading_name / legal_name (first non-empty) |
| Company domain name | `domain` | domain of website (head-office row only if shared) |
| Phone Number | `phone` | best phone, E.164 |
| Street Address | `address` | street |
| Street Address 2 | `address2` | complement |
| City | `city` | city |
| State/Region | `state` | region |
| Postal Code | `zip` | postal_code |
| Country/Region | `country` | country |
| Website URL | `website` | website |
| LinkedIn Company Page | `linkedin_company_page` | linkedin |
| Number of Employees | `numberofemployees` | financials.employees (numeric only; SIRENE gives bands) |
| Annual Revenue | `annualrevenue` | financials.revenue_eur (latest year) |

Not mapped on purpose: standard **Industry** is a fixed enumeration (NAF does not map cleanly; we use `Code NAF` and `Libelle NAF`), and **Number of Employees** is filled only from real numeric values (SIRENE gives bands, stored in `Tranche effectifs`).

## Mapping: custom properties to create (client action)
Create each with the exact **label** shown (headers auto-map by label), group "France Business Data".

| CSV header (label) | Suggested internal name | Type | Source (internal) |
|---|---|---|---|
| SIRET | `siret` | Single-line text, UNIQUE value | company_addresses.siret |
| SIREN | `siren` | Single-line text | companies.siren |
| Raison sociale | `raison_sociale` | Single-line text | companies.legal_name |
| Code NAF | `code_naf` | Single-line text | naf |
| Libelle NAF | `libelle_naf` | Single-line text | naf_reference.label |
| Metier | `metier` | Single-line text | naf_reference.trade |
| Forme juridique | `forme_juridique` | Single-line text | legal_form_label |
| Siege | `est_siege` | Single checkbox (true/false) | is_head_office |
| Statut etablissement | `statut_etablissement` | Dropdown (active, closed, other) | company_addresses.status |
| Date creation | `date_creation` | Date picker | creation_date (YYYY-MM-DD) |
| Date cloture | `date_cloture` | Date picker | closure_date |
| Departement code | `departement_code` | Single-line text | department_code |
| Departement | `departement_nom` | Single-line text | department_name |
| Region | `region` | Single-line text | region |
| Categorie taille | `categorie_taille` | Dropdown (TPE, PME, ETI, GE) | size_category |
| Tranche effectifs | `tranche_effectifs` | Single-line text | employee_range |
| Annee CA | `annee_ca` | Number | revenue_year |
| Certifications | `certifications` | Single-line text | company_certifications (active, joined with ;) |
| Dirigeant prenom | `dirigeant_prenom` | Single-line text | company_directors.first_name |
| Dirigeant nom | `dirigeant_nom` | Single-line text | company_directors.last_name |
| Dirigeant fonction | `dirigeant_fonction` | Single-line text | company_directors.role |
| Email professionnel | `email_pro` | Single-line text (not the contact Email) | company_contacts (email, never inferred) |
| Statut email | `statut_email` | Dropdown (verified, sourced, unavailable) | company_contacts.value_status |
| Statut telephone | `statut_telephone` | Dropdown (verified, sourced, unavailable) | company_contacts.value_status |
| Source contact | `source_contact` | Single-line text | data_sources.code of contact |
| Source principale | `source_principale` | Single-line text | data_sources.code |
| Date verification | `date_verification` | Date picker | last_verified_at (date) |
| Score confiance | `score_confiance` | Number (0-1) | confidence_score |
| Fraicheur donnees | `fraicheur_donnees` | Dropdown (fresh, stale, expired) | freshness_status |
| Note Google | `note_google` | Number | company_reviews.rating (only if provider ToS allow) |
| Nombre avis Google | `nb_avis_google` | Number | company_reviews.review_count |
| URL Google | `url_google` | Single-line text | company_reviews.profile_url |
| ID interne | `id_interne` | Number | establishment_id |

## Pre-import validation checklist
- [ ] All custom properties created with exact labels; dropdown options created
- [ ] File is UTF-8, header row identical to `docs`/`HEADERS`, no merged/extra columns
- [ ] Row count per file < 1,048,576 and within the daily limit of the plan
- [ ] Every row has Company name; SIRET valid (Luhn) and unique within the file
- [ ] No non-diffusible units; no closed units unless intentionally included
- [ ] Phones are E.164; no inferred emails; dates are YYYY-MM-DD
- [ ] Error report reviewed: 0 unexplained errors; warnings understood (blanked fields)
- [ ] 100-row test import done in a test portal and records inspected (dedupe behaviour, domain handling)
- [ ] Backup/export of existing HubSpot companies taken
- [ ] Legal basis, opt-out/suppression list and privacy notice checked (see security/privacy doc)

## Error report format (`hubspot_errors_YYYY-MM-DD.csv`)
Columns: `row_number, siret, id_interne, severity, code, field, message, value`.
- `error` = row **rejected** (not in the import file). Codes: `NON_DIFFUSIBLE, MISSING_NAME, INVALID_SIRET, INVALID_SIREN, SIREN_SIRET_MISMATCH, NOT_ACTIVE, INVALID_POSTAL_CODE, NO_LOCATION, DUPLICATE_SIRET`.
- `warning` = row exported, the named field **blanked**. Codes: `INVALID_PHONE, INVALID_EMAIL, INFERRED_EMAIL, BAD_DATE, BAD_CONFIDENCE, BAD_NUMBER, DOMAIN_SHARED`.
HubSpot's own import error file (downloaded after import) should be reconciled against this report.

## Sample
`examples/hubspot_companies_SAMPLE_FICTIONAL.csv`: 5 **fictional** records (names marked "EXEMPLE FICTIF", reserved phone ranges, `.example` domains, generated SIREN/SIRET that are not real). Row 2 demonstrates a second establishment of the same company (domain blanked, see `hubspot_errors_SAMPLE_FICTIONAL.csv`). Regenerate with `python scripts/make_sample.py`. Never import it.
