# 1. Executive Summary

## Goal
Build a deduplicated, enriched database of French businesses and artisans and export it as a
HubSpot-ready CSV, refreshed weekly. No website, dashboard or frontend.

## Approach
- **Backbone = SIRENE (INSEE)**: the only free, official, nationwide register. It gives
  legal units (SIREN) and establishments (SIRET), NAF/APE, legal form, status, address, dates.
- **Official enrichment**: Annuaire des Entreprises API / INPI RNE (directors, financial filings),
  BODACC (legal events), ADEME RGE (certifications), Repertoire des metiers via SIRENE artisan flags.
- **Paid/licensed enrichment (optional)**: Pappers or similar (revenue, headcount, directors),
  Google Places (ratings), a licensed provider for phone/email (e.g. Solocal data licence).
- **Storage**: PostgreSQL, Python ETL, weekly scheduled run, change history table.
- **Output**: one CSV for HubSpot Companies (+ optional separate Contacts CSV).

## What is realistic
| Item | Realistic expectation |
|---|---|
| Establishment records (SIRENE active) | Several million; filtered subset of 500k-1M+ is easily reachable |
| Records with address, NAF, status, dates | ~100% (official) |
| Records with phone | Low from open data; needs a licensed source |
| Records with professional email | Low; needs a licensed source, must not be inferred |
| Records with directors | High for companies (legal entities), low/none for sole proprietors |
| Records with revenue | Minority (many small firms do not publish accounts) |
| Google rating | Subject to Google ToS (limited storage); matched per record, paid per request |

**We do not promise 1M fully enriched records.** We promise 1M+ clean registry records, with
enrichment coverage measured and reported per field.

## Key risks (detail in 16)
1. Phone/email are not in SIRENE: coverage depends on a licensed source.
2. Pages Jaunes forbids scraping; use only a contractual data licence.
3. Google Places data cannot be bulk-stored freely.
4. GDPR/CNIL: sole proprietors and named directors are personal data.
5. SIRENE "non-diffusible" units must be excluded.
