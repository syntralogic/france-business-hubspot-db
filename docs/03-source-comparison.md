# 4. Source Comparison Matrix

Facts below were checked against official/vendor pages on **13 Oct 2026** where marked ✔ (see `docs/13-verification-log.md`). Rows marked ⚠ could **not** be verified and must be confirmed with the vendor before contracting.

| Source | Provides | Access / cost | Bulk / API | Coverage & limits | Commercial prospecting + HubSpot storage | Update | Integration |
|---|---|---|---|---|---|---|---|
| **INSEE SIRENE** ✔ | SIREN, SIRET, names, NAF, legal form, status, address, creation/closure dates, headcount range, artisan (RM) flag, ESS flag | Free (open licence) | Bulk stock files on data.gouv.fr + REST API 3.11 (free account and key on portail-api.insee.fr, header `X-INSEE-Api-Key-Integration`); quota **30 requests/min**, cursor pagination up to 1,000 results per page | Full register; non-diffusible units hidden; no phone/email; sole proprietor data limited | Yes under Licence Ouverte (attribution), respect non-diffusion + GDPR | Daily at INSEE; bulk monthly | Bulk COPY for initial load, API `dateDernierTraitement` filter for weekly delta |
| **Annuaire des Entreprises API (recherche-entreprises)** | Directors, some financial figures, legal data, convention collective, labels (RGE, ESS...) | Free, no key, rate-limited | REST API | Good for companies, weaker for sole proprietors; sampled enrichment, not a bulk dump | Yes (open data, Etalab) | Periodic | Per-SIREN lookups, throttle ~7 req/s; cache |
| **INPI RNE (Registre National des Entreprises)** | Directors/officers, legal acts, filed accounts | Free with account; some data via paid/other APIs | API + bulk stock | Directors for commercial companies; accounts only when filed and non-confidential | Yes for open parts; personal data (directors) must be handled under GDPR | Daily | API with account tokens, bulk for backfill |
| **BODACC (DILA)** | Legal events: creation, sales, procedures, closures | Free | Open data API/bulk | Event-level, not full profile | Yes (open licence) | Daily | Incremental by publication date |
| **ADEME RGE** | Eco-renovation certified firms (RGE qualifications) | Free | Open data API | Only RGE-certified building trades | Yes (open) | Regular | Join on SIRET |
| **Other certification registers (Qualibat, Qualifelec, etc.)** | Professional qualifications | Mostly web directories, terms vary | Rarely API | Not systematically open | Check each ToS; likely licence needed | Varies | Case-by-case, only if licensed |
| **Pappers API** ⚠ | Directors, financials, headcount, beneficial owners, enriched search | Paid credits/subscription | REST API | Strong coverage of companies, fewer for micro-entrepreneurs | Per Pappers terms (must verify redistribution/storage rights) | Near real time | Per-SIREN calls, budget by credits |
| **Pages Jaunes / Solocal** ⚠ | Phone, website, category, address | Scraping prohibited by ToS; data only via commercial licence | No public API; licensed data feed | Wide for local businesses | Only under written licence | Per contract | Not used until licence obtained |
| **Google Places API** ✔ | Rating, review count, Maps URL, phone, website | Paid per request | REST API | Must match by name+address. Google's service-specific terms: only `place_id` may be cached without limit; Places lat/lng for up to 30 consecutive days; no caching exception for rating or review count | **Not allowed** to keep ratings/review counts as stored HubSpot properties under standard terms. Store `place_id` only; ratings need written agreement with Google or a licensed provider | Real time | On-demand lookup, no bulk storage; deliverable columns for rating/reviews stay empty unless licensed |
| **Licensed B2B contact providers (Kompass, Dun & Bradstreet, Cognism, Lusha, etc.)** | Phone, email, websites, headcount, revenue | Paid | API or file feeds | Variable accuracy; check GDPR basis | Per contract; verify sourcing compliance | Per contract | File/API ingestion into staging |
| **Company websites / LinkedIn** | Website URL, social URLs | LinkedIn: no scraping (ToS). Websites: robots.txt/ToS respected | - | Only use URLs obtained from permitted sources | Do not scrape LinkedIn | - | URL fields from licensed/official sources only |

## Source priority (for conflict resolution)
1. SIRENE / INPI RNE (legal identity, address, status, dates)
2. Official open data (BODACC, ADEME) for events and certifications
3. Paid structured providers (Pappers etc.) for financials/headcount
4. Licensed directory data (phone, email, website)
5. Google Places (rating only; never overrides registry data)

## Field availability
| Field group | Primary source | Reliability | Update freq |
|---|---|---|---|
| Identity, NAF, legal form, status, dates | SIRENE | High | Daily/weekly |
| Address | SIRENE (+ BAN geocoding, free) | High | Weekly |
| Coordinates | SIRENE (Lambert) or BAN API | Medium-High | On change |
| Phone / email | Licensed provider only | Medium | Per contract |
| Website / LinkedIn | Licensed provider | Medium | Per contract |
| Directors | INPI RNE / Annuaire / Pappers | High (companies) | Weekly |
| Revenue / headcount | Pappers/INPI filings; SIRENE headcount range | Medium (partial) | Annual |
| Certifications | ADEME RGE, others | High where open | Monthly |
| Google rating | Google Places | Medium | On refresh |
