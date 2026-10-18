# 19 - Enrichment gaps added after the French spec review

All modules below follow the Pappers/Places pattern (daily cap through `fbdb.common.usage`, `--limit` first run, nothing invented).
**None was run against a live provider** (sandbox network is blocked): response field names come from provider docs and must be
confirmed with `--limit 5` and the log read.

| Module | Provider | Paid | What it stores |
|---|---|---|---|
| `fbdb.enrich.website` | Firecrawl | yes | website marked `verified` ONLY if our SIREN is on the site; proof in `match_proof`/`match_url`; social links found on that site |
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
- Not built yet: automatic extraction of team pages (the website module does not yet read team/contact pages for names), and a HubSpot export of the extra contacts.
