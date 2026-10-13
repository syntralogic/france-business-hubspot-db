# 12. Python Project Structure and Key Implementation Examples

## Layout (as in this repository)
```
france-business-hubspot-db/
├── docs/                      blueprint documents (index: docs/00-index.md)
├── sql/                       001 core schema, 002 staging, 003 pipeline state, 004 export view, 005 directors bookkeeping
├── src/fbdb/
│   ├── extract/               sirene_bulk.py (CSV stock mapping), sirene_api.py (delta client, 30 req/min throttle, retries)
│   ├── transform/             normalize.py (SIREN/SIRET Luhn, names, addresses, phones E.164, emails, domains)
│   ├── load/                  sirene_loader.py (COPY -> staging -> upsert), weekly.py (delta, change history, report)
│   ├── match/                 dedup.py (identifier ladder + fuzzy), resolve.py (source priority / recency / confidence)
│   ├── enrich/                annuaire.py (API client), directors.py (weekly directors refresh)
│   └── export/                hubspot.py (columns, validation, error report, file split), run_export.py (CLI)
├── scripts/                   run_weekly.sh (cron wrapper), make_sample.py, gen_schema_doc.py
├── examples/                  FICTIONAL sample CSV + error report (never import)
├── tests/                     unit tests + integration/ (real PostgreSQL via TEST_DATABASE_URL)
├── data/, reports/            local outputs, git-ignored
└── .env.example               variable names only; real values stay in .env (git-ignored)
```
Not implemented yet (documented as future work, no stub code): Pappers financials, ADEME RGE certifications, licensed phone/email, Google ratings, BAN geocoding.

## Key implementation 1: normalization for matching (`transform/normalize.py`)
Names are compared after removing accents, punctuation and legal-form words, so `Boulangerie Dupont S.A.R.L.` and `BOULANGERIE DUPONT` meet:
```python
def normalize_company_name(name):
    s = strip_accents(name).upper()
    s = re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m.group(0).replace(".", ""), s)   # S.A.R.L. -> SARL
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in LEGAL_FORM_NOISE)
```
SIREN/SIRET are validated with the Luhn check (including the La Poste exception), phones with `phonenumbers` (E.164), domains reduced to the registrable host.

## Key implementation 2: duplicate decision (`match/dedup.py`)
Identifiers decide first; fuzzy evidence is only used when an identifier is missing on a side:
```python
def compare(a, b):
    if same SIRET:                      return AUTO_MATCH
    if both SIREN valid and equal:      return SAME_COMPANY_OTHER_ESTABLISHMENT   # never merged
    if both SIREN valid and different:  return DISTINCT                           # whatever the name
    # no identifier on one side: name similarity (rapidfuzz token_sort_ratio) + phone / email / domain / address
    if name_sim >= NAME_WEAK   and (phone or email or domain equal): return AUTO_MATCH
    if name_sim >= NAME_STRONG and same standardized address:         return AUTO_MATCH
    if name_sim >= NAME_REVIEW and (same address or same postal code): return REVIEW   # human queue
    return DISTINCT   # same address with a different name, or same name only, is not a duplicate
```
Every decision returns its reasons (`same_phone`, `name_similarity=93`, ...) so a reviewer can see why. See [08-dedup-strategy.md](08-dedup-strategy.md).

## Key implementation 3: weekly delta with change history (`load/weekly.py`)
The diff SQL runs **before** the upserts so old values still exist; the same statement list is idempotent (a second run finds no differences). The batch row is marked `failed` and the transaction rolled back on error, and the watermark only advances on success.

## Key implementation 4: directors refresh (`enrich/directors.py`)
```python
active = {(last, first, role): director_id ...}          # current active rows
wanted = {(m.last, m.first, m.role): m for m in map_dirigeants(api_result)}
new      = wanted - active   -> INSERT (reactivates a returning director), history 'insert'
departed = active - wanted   -> is_active = FALSE, end_date = today, history 'delete'
unchanged                    -> last_verified_at = now()
```
Natural persons only, birth year only, never for sole proprietors; per-company failures leave `directors_checked_at` empty so the company is retried next run and the batch ends `partial`.

## Key implementation 5: HubSpot export (`export/hubspot.py`)
`build_row` maps a view row to the CSV columns, `validate_row` returns issues (`error` rejects the row, `warning` blanks one field), `export_companies` writes UTF-8 files (no BOM) split below HubSpot's 1,048,576-row limit plus the error report. Mapping and checklist: [07-hubspot-mapping.md](07-hubspot-mapping.md).

## Run it
See the README quick start. Tests: `pytest` (unit) and `TEST_DATABASE_URL=... pytest tests/integration` (PostgreSQL).
