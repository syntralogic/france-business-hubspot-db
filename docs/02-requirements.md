# 2. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | Ingest SIRENE bulk stock (legal units + establishments) and weekly increments |
| FR-02 | Store raw source payloads untouched, normalize into a staging layer |
| FR-03 | Maintain master `companies` (SIREN) and `establishments` (SIRET) |
| FR-04 | Normalize names, addresses, phones (E.164), emails, domains |
| FR-05 | Deduplicate: exact SIREN/SIRET first, then fuzzy for non-registry sources |
| FR-06 | Enrich with directors, financials, certifications, reviews, contacts when source permits |
| FR-07 | Track source, retrieval date and status (verified/sourced/inferred/unavailable) per field |
| FR-08 | Resolve conflicts by source priority, recency and confidence |
| FR-09 | Detect new, changed, closed records weekly and log old/new values |
| FR-10 | Produce weekly report: added, updated, closed, unchanged, needs review |
| FR-11 | Export HubSpot Companies CSV (UTF-8) and optional Contacts CSV |
| FR-12 | Validate export and produce rejected-records error report |
| FR-13 | Exclude non-diffusible records and honour opt-out/suppression list |

# 3. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | Scale to 1M+ active establishments, 10M+ stock rows loaded |
| NFR-02 | Weekly run completes in < 6 h on a modest server (8 vCPU, 32 GB) |
| NFR-03 | Idempotent runs: re-running a week must not create duplicates |
| NFR-04 | Retries with exponential backoff, resumable batches |
| NFR-05 | Secrets via environment/secret manager, never in repo |
| NFR-06 | Full audit trail (change history, batch logs) |
| NFR-07 | GDPR: purpose limitation, minimization, retention policy, deletion on request |
| NFR-08 | Data accuracy targets: registry fields 99%+, validated phones/emails flagged by status |
| NFR-09 | Reproducible deployment (Docker Compose or documented setup) |
| NFR-10 | Tests on normalization, matching, export validation |

# Assumptions (impact)
- Client has a HubSpot plan that allows custom company properties (most paid tiers do; Free has limits). Impact: if not, extra fields go in a notes property.
- Scope = metropolitan France + DOM unless client restricts. Impact: volume.
- "Active" establishments only for the main deliverable; closed ones kept in DB for change tracking.
- Contact data (phone/email) coverage depends on a licence the client may need to buy.
