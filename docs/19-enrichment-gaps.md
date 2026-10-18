# 19 - Enrichment gaps added after the French spec review

All modules below follow the Pappers/Places pattern (daily cap through `fbdb.common.usage`, `--limit` first run, nothing invented).
**None was run against a live provider** (sandbox network is blocked): response field names come from provider docs and must be
confirmed with `--limit 5` and the log read.

| Module | Provider | Paid | What it stores |
|---|---|---|---|
| `fbdb.enrich.website` | Firecrawl | yes | website marked `verified` ONLY if our SIREN is on the site; proof in `match_proof`/`match_url`; social links found on that site |
| `fbdb.enrich.site_extract` | - (reads pages already scraped by `website`) | no | phones (tel:/text), emails (mailto:/text), named people (legal-notice formulas, name next to a function) from a site PROVEN by SIREN |
| `fbdb.enrich.email_verify` | Hunter | yes | verdict in `verify_result`; invalid/disposable -> `unavailable` |
| `fbdb.enrich.phone_lookup` | Twilio Lookup | yes | `line_type`; invalid number -> `unavailable` |
| `fbdb.enrich.certifications` | licence/client CSV | contract | Qualibat, Qualifelec, Qualit'EnR, RC pro, Decennale (RGE stays in `ademe_rge`) |
| `fbdb.enrich.website_discovery` | Firecrawl search + scrape | yes | finds a site for companies with none; stored ONLY if our SIREN is on the home/legal/contact page; directories and social networks are never candidates; `website_searches` remembers the outcome (`not_proven` is not "no website") |
| `fbdb.enrich.interlocutors` | - | no | several people per company (`company_interlocutors`); register directors copied with their legal role; phones/emails linked to a person ONLY with an evidence level; confirmed decision maker needs a note |
| `fbdb.common.provenance` | - | no | `field_conflicts` rows; opposition per CHANNEL (`contact_oppositions`: email / call / sms), keyed by value |

Schema: `sql/003a_provenance.sql` (runs before 004 on purpose). The HubSpot view excludes a phone opposed for `call` (the next allowed phone is exported instead) and an email opposed for `email`; the legacy `company_contacts.opposed_at` flag is still honoured (fail closed) but no longer written.

Still open (needs a decision or data): Dropcontact (alternative to Hunter),
~~integration of `record_conflict` into `match/resolve.py`~~ (done: `resolve.persist_conflicts`; `resolve()` itself is not used by any loader yet, so the live conflict recording is in `licensed_contacts.store_contact` for websites).
Contracts/ToS to check: Firecrawl scraping of third-party sites, Hunter/Twilio storage terms, Qualibat/Qualifelec directory terms.

Website discovery is NOT in `run_weekly.sh` (paid, manual like `fbdb.enrich.website`). Before running at scale: `--limit 5`, check the Firecrawl search response and credit cost,
then label ~100 results by hand to measure the wrong-match rate. Cost per company is at most 1 search + 2 candidates x 3 pages, all inside `FIRECRAWL_DAILY_CAP`.

## Opposition per channel (how to use it)
```
python -m fbdb.common.provenance oppose --value jean@exemple.fr                  # email channel
python -m fbdb.common.provenance oppose --value 0612345678 --channel sms          # stop SMS, calls still allowed
python -m fbdb.common.provenance oppose --value 0612345678 --channel all         # call + sms (default for a phone)
python -m fbdb.common.provenance status --value 0612345678
```
- Keyed by the normalized VALUE: it covers every company, rows added later, re-imports, and values we do not hold yet.
- The HubSpot CSV has no channel column: its phone is treated as a CALL number. SMS opposition is exposed as the view column `phone_sms_allowed` (not in the CSV);
  any SMS tool built on this data must read it (or `contact_oppositions`).
- `email_verify` / `phone_lookup` stop spending credits on a value opposed on every channel.
- Not built: lifting an opposition (delete the `contact_oppositions` row by hand, only if the person gave a documented new consent) and opposition on social/website contacts.
- Legal basis per channel (B2B opt-out vs B2C consent, Bloctel, sole proprietors) is NOT decided by this code: see docs/09-security-privacy.md and have counsel confirm.

## Interlocutors (several contacts per company, spec section 5)
```
python -m fbdb.enrich.interlocutors sync-directors                      # register directors -> interlocutors (no contact invented)
python -m fbdb.enrich.interlocutors add --siren 552100554 --last-name Durand --first-name Eva --function "Responsable commercial" --source team_page --source-url https://...
python -m fbdb.enrich.interlocutors link --siren 552100554 --value 0698765432 --last-name Durand --evidence published_with_name --use sales
python -m fbdb.enrich.interlocutors confirm --siren 552100554 --last-name Martin --note "confirmed on call 2026-10-20"
python -m fbdb.enrich.interlocutors list --siren 552100554
```
- Schema `sql/013_interlocutors.sql`. Legal role (register) and operational function (team page) are separate fields; `function_category` is a keyword classification (director, sales, project_manager, admin, technical, other, unknown) to be checked by a person.
- A number or email is linked to a person only with `published_with_name`, `provider_attributed` or `confirmed_by_contact` (database CHECK). Without a link it stays "company - person not identified". A link never creates a value, never downgrades evidence and refuses a value already owned by another person.
- `decision_maker_confirmed` needs a note and a date (database CHECK): a job title is not proof.
- View `v_interlocutor_contacts` (one row per person x contact) hides values opposed on the call/email channel. The HubSpot company view is unchanged (still one phone/email per company).

## Phones, emails and people read from the official site (spec sections 5-8)
`fbdb.enrich.website` (and `website_discovery`) now keep going after the SIREN proof: they scrape up to 2 more same-site pages named contact / equipe / a-propos / ... (total max 4 pages per site, still inside `FIRECRAWL_DAILY_CAP`), then `site_extract` reads every scraped page.
- Only a site PROVEN by SIREN feeds this; nothing is stored from an unproven site.
- Phones and emails go to `company_contacts` (`value_status = sourced`, source `website`, `match_url` = the page, `match_proof` = the line + the SIREN proof, `contact_use` guessed from the label or address: switchboard / direction / sales / emergency / after_sales / accounting / recruitment / general). A number on a page without a name stays "company - person not identified" (never the director's mobile by default). Existing values are never modified.
- People: legal-notice formulas ("Directeur de la publication : ...") and a name next to a function keyword. The same person already known (register director) is reused, not duplicated. A phone/email is linked to a person (`published_with_name`) only when it sits on the person's line or on the line right after it.
- Email verification (`email_verify`) and line type (`phone_lookup`) pick the new values up automatically.
- The name heuristic is NOT validated on real sites: run `python -m fbdb.enrich.website --limit 5`, read the people found, and label ~50 sites by hand to measure precision before using `function_category` for targeting.

## HubSpot Contacts export (people)
`python -m fbdb.export.hubspot_contacts --out data/exports` (also a step in `scripts/run_weekly.sh`, same `EXPORT_AFTER_WEEKLY` switch) writes `hubspot_contacts_<date>_partNN.csv` from the view `v_hubspot_contacts` (`sql/014`).
- One row per ACTIVE interlocutor with at least one usable value. Only values linked to that person are used; `verified`/`sourced` only; nothing opposed on the channel (email -> email, phone -> call); invalid values are blanked again at export.
- `+336/+337` goes to "Mobile Phone Number", any other phone to "Phone Number" (prefix hint, not proof of ownership). The evidence level of each value is exported (`Preuve email/telephone/mobile`); when several values compete for a slot the best evidence wins.
- "Company domain name" is the proven website; without one the contact is associated by company name + the custom property SIREN. Create the custom properties (SIREN, Fonction categorie, Decideur confirme, Preuve email, Preuve telephone, Preuve mobile) before the import, and test with 100 rows first like the companies import.
- SMS tools must still read `contact_oppositions` / `phone_sms_allowed`: this CSV is a call/email export.

## Direct email of a named person (spec section 7)
`python -m fbdb.enrich.email_find --limit 5` (Hunter Email Finder, paid, `HUNTER_FINDER_DAILY_CAP`, not in `run_weekly.sh`; migration `sql/015`).
- Searches active interlocutors with a first + last name, no email linked yet, whose company has a website proven by SIREN (domain taken from that site). Default: `director` only (`--categories director,sales` to widen).
- Never builds `prenom.nom@domaine` itself. Stored with source `hunter`, linked to the person with evidence `provider_attributed`: verification `valid` -> `verified`; `invalid`/`disposable` -> not stored; `accept_all`/`unknown`/none -> `inferred` (a hypothesis, NOT exported until qualified).
- An address opposed on the email channel is never stored. Each attempt is remembered (`email_find_attempts`), so nobody is paid for twice before `--refresh-days` (180).
- Not built: Dropcontact (alternative finder; decide after the Hunter hit-rate test) and a provider for DIRECT MOBILES of directors (Lusha-type contract needed: test its coverage on the client's artisans first). Until then a mobile only exists when published on the site or loaded from a licensed file, and stays "company - person not identified" unless a name sits next to it.

## First test lot: what to measure (client's last paragraph)
`python -m fbdb.report.metrics` now reports (report only, no target agreed): `match_companies_with_proven_website`, `match_companies_with_interlocutor`, `match_companies_with_social_account`, `match_directors_with_mobile_linked` (a +336/+337 number attached to the director with evidence, not merely held by the company), `match_directors_with_verified_email_linked`, `match_directors_with_any_email_linked` (includes provider hypotheses), `match_directors_decision_maker_confirmed`.
Suggested order for the test lot of ~100 companies (all paid steps start with `--limit 5`, read the log, then widen):
1. `python -m fbdb.enrich.directors` (free), then `python -m fbdb.enrich.interlocutors sync-directors`
2. `python -m fbdb.enrich.website` (or `website_discovery` for companies with no site) -> proven site, socials, phones, emails, people
3. `python -m fbdb.enrich.email_find --limit 5` -> `python -m fbdb.enrich.email_verify` -> `python -m fbdb.enrich.phone_lookup`
4. `python -m fbdb.report.metrics`, then export (`fbdb.export.run_export`, `fbdb.export.hubspot_contacts`) and a 100-row HubSpot test import.
