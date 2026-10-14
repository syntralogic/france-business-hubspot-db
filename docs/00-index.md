# Documentation index

The client brief asks for 16 sections in a fixed order. This table maps each one to its file. File numbers are historical and do not always equal the section number; the section number is in each file's title.

| Section | Content | File |
|---|---|---|
| 1 | Executive summary | [01-executive-summary.md](01-executive-summary.md) |
| 2 | Functional requirements | [02-requirements.md](02-requirements.md) |
| 3 | Non-functional requirements | [02-requirements.md](02-requirements.md) (second heading) |
| 4 | Source comparison matrix | [03-source-comparison.md](03-source-comparison.md) |
| 5 | Architecture diagram (Mermaid), data flow, stack, storage | [04-architecture.md](04-architecture.md) |
| 6 | Detailed relational schema (keys, constraints, indexes, identifiers) | [04a-relational-schema.md](04a-relational-schema.md), DDL in `sql/` |
| 7 | Field-level data dictionary | [05-data-dictionary.md](05-data-dictionary.md) |
| 8 | Deduplication strategy | [08-dedup-strategy.md](08-dedup-strategy.md) |
| 9 | Weekly ETL workflow | [06-weekly-etl.md](06-weekly-etl.md) |
| 10 | HubSpot field mapping, import guide, checklist, error report | [07-hubspot-mapping.md](07-hubspot-mapping.md) |
| 11 | Example export dataset (5 fictional records) | `examples/hubspot_companies_SAMPLE_FICTIONAL.csv`, described in section 10 of [07-hubspot-mapping.md](07-hubspot-mapping.md) |
| 12 | Python structure and key implementation examples | [12a-python-structure.md](12a-python-structure.md) |
| 13 | Security, privacy, data licensing | [09-security-privacy.md](09-security-privacy.md) |
| 14 | Testing and acceptance criteria | [10-testing-acceptance.md](10-testing-acceptance.md) |
| 15 | Timeline and cost breakdown | [11-timeline-cost.md](11-timeline-cost.md) |
| 16 | Risks, assumptions, open questions | [12-risks-assumptions.md](12-risks-assumptions.md) |
| App. A | Verification log (sources and HubSpot facts checked on 13 Oct 2026) | [13-verification-log.md](13-verification-log.md) |
| App. B | Licensed phone/email file loader (provider-agnostic, contract required) | [14-licensed-contacts.md](14-licensed-contacts.md) |
| App. C | Deployment, scheduling, logs, monitoring, alerts, on-server acceptance checklist | [15-deployment.md](15-deployment.md) |
| App. D | Client decisions, data-provider checklist, email draft | [16-client-questions.md](16-client-questions.md) |
| App. E | Render deployment (Blueprint, worker with disk, in-container scheduler) | [17-render-deployment.md](17-render-deployment.md) |
