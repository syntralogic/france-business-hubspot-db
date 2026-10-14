# Appendix D. Client questions, data-provider checklist and email draft

Open items that only the client can decide (see also `12-risks-assumptions.md`). None of them block the SIRENE-based
core of the pipeline; they decide scope, contact-data coverage and cost.

## A. Decisions needed from the client
| # | Question | Why it matters | Default if no answer |
|---|---|---|---|
| 1 | Which sectors, departments and size filters define the 500k to 1M target? | Sets volume, load time, hosting size | All active establishments, metropolitan France + DOM |
| 2 | Include sole proprietors (EI, micro-entrepreneurs)? | Highest privacy sensitivity (personal data); large share of artisans | Include, with non-diffusible units always excluded |
| 3 | HubSpot subscription tier and existing company data? | Import limits, custom properties, dedupe against existing records | Manual CSV import, one company per SIRET |
| 4 | Budget and preference for phone/email providers? Existing licences? | Phone/email fill rate depends entirely on a licensed source | Report fill rate per source, no invented values |
| 5 | Who is the data controller? Is there a DPO? | Legal basis, retention, suppression list | Client is controller; counsel review before first import |
| 6 | Weekly HubSpot import manual or through the API? | Automation scope and plan requirements | Manual weekly delta file |
| 7 | Are paid enrichments wanted (Pappers revenue/headcount, Google Places ratings, INPI)? Monthly cap? | Recurring cost; keys and daily caps are already supported | Off until approved |

## B. Checklist when evaluating a phone/email data provider
Ask for the answers in writing before signing; `docs/14-licensed-contacts.md` describes the file format the loader expects.
1. Is the data licensed for B2B commercial prospecting in France, and for storage in a CRM (HubSpot)?
2. Which fields per record: switchboard phone, direct line, generic email, named email? Which are verified, and when?
3. Source and collection method of each field; GDPR/CNIL compliance statement and opt-out (Bloctel does not apply to B2B, but suppression lists do).
4. Matching key: SIREN/SIRET included in every row? Match rate on a sample of the client's own SIRET list.
5. Update frequency and whether deltas are delivered; licence duration; what happens to stored data at expiry.
6. Redistribution limits (can data stay in HubSpot after the contract ends?), per-record or flat pricing, minimum volume.
7. Free test file (for example 1,000 SIRET chosen by us) to measure real coverage before buying.

## C. Email draft (to client, edit before sending)
Subject: France business database: decisions needed to finalise scope and cost

Hello,

The pipeline (SIRENE load, weekly updates, deduplication, HubSpot-ready CSV export) is built and tested. To finish scope
and give you a reliable cost, I need your decisions on the points below:

1. Target scope: which sectors, departments and company sizes make up the 500,000 to 1,000,000 records?
2. Sole proprietors (EI / micro-entrepreneurs): include them or not? They are numerous among artisans but carry the
   most personal-data sensitivity.
3. Your HubSpot subscription tier, and whether HubSpot already holds company records we must avoid duplicating.
4. Phone and email: public registers do not provide them at scale. Do you already hold a licence with a data provider,
   or should I shortlist providers and request a free test sample? Coverage depends on that choice and I will not
   invent contact data.
5. Optional paid enrichments (revenue and headcount through Pappers, Google ratings, INPI cross-checks): wanted, and
   with which monthly cap?
6. Who is the data controller on your side (and the DPO if there is one)? A short legal review before the first import is
   recommended.
7. Weekly import into HubSpot: manual CSV each week, or automated through the API (depends on your plan)?

Until I hear from you I will assume: all active establishments in metropolitan France and the DOM, sole proprietors
included except non-diffusible units, manual weekly CSV import, no paid enrichment.

Best regards
