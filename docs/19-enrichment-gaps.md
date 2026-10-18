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
| `fbdb.common.provenance` | - | no | `field_conflicts` rows, opposition per contact |

Schema: `sql/003a_provenance.sql` (runs before 004 on purpose). The HubSpot view now excludes contacts with `opposed_at` set.

Still open (needs a decision or data): Dropcontact (alternative to Hunter),
per-channel opposition (today one flag per contact value), ~~integration of `record_conflict` into `match/resolve.py`~~ (done: `resolve.persist_conflicts`; `resolve()` itself is not used by any loader yet, so the live conflict recording is in `licensed_contacts.store_contact` for websites).
Contracts/ToS to check: Firecrawl scraping of third-party sites, Hunter/Twilio storage terms, Qualibat/Qualifelec directory terms.

Website discovery is NOT in `run_weekly.sh` (paid, manual like `fbdb.enrich.website`). Before running at scale: `--limit 5`, check the Firecrawl search response and credit cost,
then label ~100 results by hand to measure the wrong-match rate. Cost per company is at most 1 search + 2 candidates x 3 pages, all inside `FIRECRAWL_DAILY_CAP`.
