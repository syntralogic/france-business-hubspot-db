# 8. Deduplication and Conflict Resolution

Implementation: `src/fbdb/transform/normalize.py`, `src/fbdb/match/dedup.py`, `src/fbdb/match/resolve.py` (all unit-tested).

## Definitions
- **Legal company** = SIREN. **Establishment** = SIRET (SIREN + NIC). Two SIRETs under one SIREN are *not* duplicates: they are two establishments of one company.
- **Duplicate** = two records describing the same legal company or the same establishment.

## Matching ladder (first rule that applies wins)
1. Same **SIRET** -> same establishment (certain).
2. Same **SIREN**, different SIRET -> same company, different establishment (never merged).
3. Two different valid SIRENs -> different companies, whatever the name or address.
4. No identifier on a side (directory/licensed feeds): fuzzy evidence, requiring corroboration:
   - name similarity >= 85 **and** same phone / email / website domain -> auto-match
   - name similarity >= 92 **and** same standardized address -> auto-match
   - name similarity >= 80 and same address or postal code -> **review** (human queue, `data_quality_issues`)
   - same address but different name (coworking, shared building) -> distinct
   - same name only (e.g. "Garage Durand" in two cities) -> distinct
5. Unmatched external records stay in `company_source_records` with `match_status='review'`; they never create a company without a SIREN.

## Normalization
Names: uppercase, accents removed, punctuation removed, legal-form suffixes (SARL, SAS, S.A.R.L. ...) dropped for matching only. Addresses: abbreviation expansion (AV -> AVENUE), arrondissement suffixes removed from city, SHA-1 hash of the normalized string. Phones: E.164 via libphonenumber (FR default), invalid -> None (never guessed). Emails: lowercase + syntax. Websites: bare domain.

## Conflict resolution (per field)
| Policy | Fields | Order of precedence |
|---|---|---|
| `registry` | name, legal form, NAF, status, address, dates | source priority (official first) -> recency -> confidence |
| `contact` | phone, email, website | verification status (verified > sourced) -> recency -> source priority -> confidence |

Rules: inferred values are used only if nothing else exists and are never exported as email; an exact tie between different values raises a conflict -> `data_quality_issues` for review; losing values are kept in `company_source_records` and `company_change_history` (nothing is silently overwritten).

## Confidence score (0-1, stored in `companies.confidence_score`)
Starting proposal, to be calibrated on a labelled sample in Phase 4: registry identity present (+0.5), address geocoded with BAN score >= 0.8 (+0.15), at least one verified/sourced contact (+0.15), confirmed by >= 2 independent sources (+0.1), data fresher than 30 days (+0.1), minus penalties for open conflicts.

## Measuring quality
Hand-label ~500 random candidate pairs; report precision/recall of the ladder; tune thresholds before the first production run.
