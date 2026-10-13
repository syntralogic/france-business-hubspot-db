# 13. Security, Privacy and Data Licensing

> This is an engineering checklist, not legal advice. The client (data controller) should confirm the points below with its DPO or counsel before prospecting.

## Personal data in scope
Director/representative names and roles; sole proprietors' names and addresses (the business **is** the person); professional emails that contain a person's name. Registry data on legal entities is not personal data in the same way.

## Principles applied
- **Minimization**: director birth *year* only, no home addresses, no full birth dates; only fields needed for B2B prospecting.
- **Exclusion**: SIRENE non-diffusible units (`statut diffusion` N/P) are stored flagged and **never exported** (INSEE masks identity, address and geolocation for natural persons who objected).
- **Purpose limitation**: B2B commercial prospecting only, as stated by the client.
- **Transparency**: the client must inform people whose data it holds (GDPR Art. 14, at first contact and within one month of obtaining data) and provide a clear opt-out in every message.
- **Rights**: access / rectification / erasure / objection. Maintain a **suppression list** table; every export excludes suppressed SIRET/SIREN/email/domain; deletion requests must also be pushed to HubSpot.
- **Retention**: define a retention period (proposal: closed companies kept 12 months for change tracking, personal data of directors deleted when no longer active/needed).
- **Electronic prospecting rules** (CNIL guidance and CPCE art. L34-5 as summarised by CCI Paris IDF, updated 25 Jun 2026 and CNIL commentary of Jun 2026; **not legal advice**): B2C email/SMS needs prior consent (opt-in). B2B email works on an opt-out regime **only if** the person was informed that the address would be used for prospecting, the message concerns their profession, and a simple free opposition is offered in every message. Art. 14 GDPR information is still required for indirectly collected data. Human phone prospecting to professionals needs prior information and an opposition right; Bloctel concerns consumers. Sole proprietors are natural persons: have counsel confirm the regime per record category before mailing.
- Legal representatives are **not** distributed by INSEE in open data; directors must come from INPI RNE / Annuaire des Entreprises / licensed providers, each under its own terms.

## Source licensing rules
| Source | Rule |
|---|---|
| SIRENE, BODACC, ADEME RGE, INPI open parts | Open licence (attribution), personal-data obligations still apply |
| Pages Jaunes / Solocal | **No scraping**; only via written commercial data licence |
| Google Places | Verified: only `place_id` is exempt from the caching ban; lat/lng up to 30 days. **Do not store ratings or review counts** without a written agreement |
| LinkedIn | No scraping; only URLs obtained from permitted sources |
| Pappers / other paid APIs | Check redistribution, caching and CRM-storage clauses in the contract |
| Websites | Respect robots.txt and ToS; no automated harvesting of personal emails |

## Technical security
- Secrets only in environment variables / secret manager; `.env` is git-ignored; rotate keys; separate keys per environment.
- Least-privilege DB roles (ETL writer, export reader read-only on `v_hubspot_companies`).
- Encryption at rest (disk/volume) and in transit (TLS to DB and APIs); backups encrypted, tested restore.
- Audit: `import_export_batches`, `company_change_history`, structured logs without personal data values.
- Exports contain personal data: store in restricted location, delete after import, no email attachments.
- GitHub tokens: use fine-grained, repo-scoped, short-lived; never paste into chats or commit; revoke after use.
