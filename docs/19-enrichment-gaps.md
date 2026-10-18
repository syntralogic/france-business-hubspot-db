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
| `fbdb.common.provenance` | - | no | `field_conflicts` rows, opposition per contact |

Schema: `sql/003a_provenance.sql` (runs before 004 on purpose). The HubSpot view now excludes contacts with `opposed_at` set.

Still open (needs a decision or data): Dropcontact (alternative to Hunter), website discovery by search (no proof rule yet),
per-channel opposition (today one flag per contact value), ~~integration of `record_conflict` into `match/resolve.py`~~ (done: `resolve.persist_conflicts`; `resolve()` itself is not used by any loader yet, so the live conflict recording is in `licensed_contacts.store_contact` for websites).
Contracts/ToS to check: Firecrawl scraping of third-party sites, Hunter/Twilio storage terms, Qualibat/Qualifelec directory terms.
