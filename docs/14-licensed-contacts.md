# Licensed phone / email / website ingestion

Module: `src/fbdb/enrich/licensed_contacts.py` (tests: `tests/test_licensed_contacts.py`, `tests/integration/test_licensed_contacts_pg.py`).

## What it is, and what it is not
The client's phone and email columns cannot come from the official registers (SIRENE has no phone/email). They need a licensed provider
(Pages Jaunes/Solocal, Kompass, Dun & Bradstreet, Cognism, ...). **No provider is selected or contracted yet; that is a commercial
decision and the main cost item (docs/11-timeline-cost.md).** Pages Jaunes is never scraped.

The loader is provider-agnostic: the provider delivers a CSV, the loader matches it to the register and stores the values in
`company_contacts` with source `licensed_contacts` (priority 4). It has not been run on a real provider file; the tests use fictional data.

## Usage
```
python -m fbdb.enrich.licensed_contacts FEED.csv --contract-ref ABC-2026-01 [--mapping provider.json] [--status sourced|verified]
```
- CSV: UTF-8 (BOM ok), delimiter auto-detected (`;` `,` tab `|`).
- Default columns: `siret`, `siren`, `phone`, `phone_2`, `email`, `website`, `linkedin`. Other provider layouts: pass a JSON mapping, e.g.
  `{"siret": "SIRET", "phones": ["Tel", "Mobile"], "emails": ["Mail"], "website": "Site web"}`.
- Run it when a feed is delivered (monthly/weekly per contract), not from `run_weekly.sh` automatically.

## Rules enforced
| Rule | Behaviour |
|---|---|
| Licence gate | Refuses to run without `--contract-ref` or `LICENSED_CONTACTS_CONTRACT_REF`; the reference is stored in `import_export_batches.notes`. |
| Matching | Exact only: SIRET -> establishment-level contact; else SIREN -> company-level. No fuzzy matching. Invalid check digits are rejected. |
| Scope | Active and diffusible companies only. Others are rejected (`NOT_ACTIVE`, `NON_DIFFUSIBLE`). |
| Values | Phone -> E.164 (invalid rejected), email -> lowercase syntax-checked, website -> bare domain, LinkedIn only `linkedin.com/company/...`. Never inferred. |
| Status | `sourced` by default; `verified` only if the contract guarantees provider-side verification. The export never emits `inferred`. |
| Primary | First value per company/establishment and type is primary; later values are added but never displace it. |
| History | Each new value is written to `company_change_history`. Re-running the same file adds nothing (idempotent). |
| Partial files | Never delete anything; absent values are untouched. |
| Rejects | `reports/licensed_contacts_rejects_<date>.csv` with line, ids, code, detail. Batch status becomes `partial`. |

## Before the first real load (checklist)
1. Written licence covers: B2B prospecting, storage in the database and in HubSpot, and the lawful basis/GDPR sourcing of the emails (named-person emails are personal data).
2. Obtain a sample file, fix the column mapping, run it on a copy of the database, review the rejects file.
3. Measure match rate and fill rate (docs/10-testing-acceptance.md) before promising coverage to the client.
4. Check the licence's retention/deletion clause: if the contract ends, the values from source `licensed_contacts` must be removable (`DELETE FROM company_contacts WHERE source_id = (SELECT source_id FROM data_sources WHERE code = 'licensed_contacts')`).

## Known limits
- Row-by-row matching: fine for a few hundred thousand rows per feed; for multi-million-row feeds, load into a staging table and match in SQL.
- A changed phone is added as a new value; the old one is not retired automatically (partial feeds cannot prove removal). Review via change history.
