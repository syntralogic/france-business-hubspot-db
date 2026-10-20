# Client handover: what is built, what is not, what we need from you

Reference: the client's French "mode d'emploi de collecte" (sections 1 to 13). Technical detail per item: `docs/19-enrichment-gaps.md`. Costs: `docs/11-timeline-cost.md`. Deployment: `docs/17-render-deployment.md`.

**Read this first.** All code is built and covered by automated tests (unit tests with fake clients and tests on a real PostgreSQL). **No paid or public API has been called with a real key yet** ("NOT verified live" in docs/19). The first live run is part of the acceptance, not a formality: some field names, URL shapes and keyword rules will need adjusting after it.

## What every value carries (spec section 13)
Source and URL or provider, collection date, date of the fact or exercise, last verification, level (company / establishment / person), verification status (confirmed / declared / provider / inferred / to verify), absence status (not searched / not found / inaccessible / not available), proof of the match, conflicts kept in `field_conflicts` (never silently overwritten), opposition per channel checked at every export and every paid verification. "Not found" never means "does not exist".

## Coverage of the spec
| Section | Status |
|---|---|
| 1 Identity (SIRENE, Pappers) | Built. Company and establishment kept apart, diffusion handled. |
| 2 Location | Built: address, INSEE codes, department / region, geocoding with precision. Distance to a point is computed on demand from the coordinates (no stored field). Declared service area, radius and national / regional mobility read from the site (G2b), kept apart from the address. |
| 3 Trade, services, clients | Built: trade, services, specialties, new / renovation / repair / maintenance, client types, building types, contracts, brands, approvals, commitments; each with its passage; photos never a proof. |
| 4 Size and finance | Built: one row per exercise (revenue, result, headcount, equity, cash, debt), missing never replaced by zero, comparable exercises only; the size declared on the site is kept apart from official data. |
| 5 Directors and contacts | Built: several people per company, each with own contacts, evidence level, LinkedIn only with proof. Precision of people extraction must be measured on a labelled sample (tool provided). |
| 6 Phones | Built: type, use, person attached (or "company, person not identified"), confirmation level, source and date. **Not built (needs your decision and a contract): a provider of directors' direct mobiles (Lusha type) and WhatsApp.** |
| 7 Emails | Built: published / provider-found / inferred kept apart, technical status vs recipient identity, mailbox function, priority email by editable rules. **Not built: Dropcontact (decide after the Hunter hit-rate test).** |
| 8 Official site | Built: search, SIREN proof, contact / team / services pages, accessibility status, page cap per company. |
| 9 Social accounts | Built: site links first, then targeted search, proof, status per network, last verification date. Followers and last post deliberately not collected (login wall, optional in the spec). |
| 10 Reviews | Built: Google Places (identifier, link, rating, count, date, 5-review limit stated), review-platform links (Trustpilot, Pages Jaunes). **Review texts are not collected** (authorised access and terms are your decision). Google ratings are not exported until you confirm the licence. |
| 11 Qualifications | Built: RGE (ADEME) with domain, link and validity; Qualibat / Qualifelec / Qualit'EnR through licensed files; RC pro / decennale attestations (document, hash, validity). "Declared on the site" never counts as verified. **Qualibat, Qualifelec and RC pro files need your licence / documents.** |
| 12 References and news | Built: achievements, partners, recruitment, press, public contracts (DECP, downloaded automatically), agency opening / new activity, documented legal relations only. "No result" never means "never worked for the public". |
| 13 Reliability | Built as above. |

## What we need from you
1. **Accounts and keys, on your own account and card** (we never keep them in code, chat or files): Pappers, Firecrawl, Hunter; Google Cloud (Places API New, with billing) and Twilio only if you want ratings / line type. Free keys for INSEE (SIRENE) and INPI are created by us. Details: `docs/15-deployment.md`, `.env.example`.
2. **Decisions** (`docs/16-client-questions.md`): sectors, departments, size (this sets the volume and the cost), sole traders, HubSpot plan and import method, monthly budget cap, data controller / DPO.
3. **Legal**: opposition lift procedure, retention of contacts of an establishment that stops being diffusible, withdrawn RGE rule, written confirmation that Google ratings may be stored and shown, retention of attestation documents.
4. **Licences and files**: Qualibat, Qualifelec, Qualit'EnR, licensed contacts (contract reference stored with every load); the CSV link you use for the ADEME RGE list (`RGE_CSV_URL`).
5. **Acceptance test lot**: about 100 companies and someone to label the results, to measure the real number of mobiles attached to directors, qualified direct emails, correct sites and social accounts (the first test lot described in docs/19). Please validate the editable email-priority rules (G3), which are our assumption.
6. **HubSpot**: which of the new data (activities, declared area, press, contracts, certificates) you want as custom properties; today the exports are unchanged.
7. **Decisions on what is not built**: Dropcontact, directors' direct mobile provider, WhatsApp (only when the company publishes it).

## How to run it
`scripts/run_weekly.sh` runs the automatic steps (SIRENE update, directors, geocoding, BODACC and register events, DECP every 30 days, and, when their key or link is set, INPI, Pappers, Places, ADEME). The Firecrawl, Hunter and Twilio modules are started by hand with `--limit 5` first. Every paid module has a daily cap and a memory table so that nothing is paid twice. Tests: `PYTHONPATH=src python -m pytest -q` with `TEST_DATABASE_URL` set.
