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
| `fbdb.enrich.social_search` | Firecrawl search (+ scrape of one candidate page) | yes | social accounts missing from the proven site (spec section 9): per network `confirmed` (with proof) / `to_verify` / `not_found` / `inaccessible`, memory + last verification date in `social_searches` |
| `fbdb.enrich.site_activity` (+ `activity_rules`) | Firecrawl scrape of the proven site's offer pages | yes | what the company SAYS it does (spec section 3): trade, services, specialties, neuf/renovation/depannage/maintenance, client types, contracts, brands, claimed approvals; each claim with its passage, URL, date and the site's SIREN proof |
| `fbdb.enrich.decp` | DECP open data (file you download) | no | public contracts per titulaire SIREN (spec section 12): buyer, object, amount, dates, CPV, procedure; dataset URL = proof; `decp_loads` remembers every file; "none" is derived, never stored |
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
- The name heuristic is **NOT validated on real sites** (no real site is reachable from the build sandbox: no precision figure exists yet, and none is claimed). Measure it with the tool below before using `function_category` or the person-to-contact links for targeting.

### People quality (spec section 5, step 4)
**Measuring (file based, free, repeatable)**
```
python -m fbdb.enrich.website --limit 50 --save-pages data/pages     # paid run; also writes the pages of each PROVEN site: data/pages/<siren>__NN.md
python -m fbdb.report.people_eval sample data/pages --out to_label.csv --sites 50
#   a person fills person / function_ok / category_ok / contacts_ok (yes|no|na); rows added by hand with extra=missed feed recall
python -m fbdb.report.people_eval score to_label.csv --out reports/people_eval.json
```
`sample` lists every person the extractor returns on ~50 random sites (rule, line, function, predicted category, attached phone / email, page URL) and says how many sites returned nobody. `score` gives person precision overall and per rule (`legal_notice` vs `name_and_function`), function / category / contact-attachment precision and an indicative recall. Because the pages are saved, every rule change can be re-scored on the SAME pages without paying for a scrape again. **No acceptance threshold is invented**: the client decides it per use (attaching a phone to a person needs far more precision than a list of names). `--save-pages` writes only proven sites.

**Rules improved (found by stress-testing typical page texts; each has a regression test, none measured on real sites)**
- A first name is never a place word (`Saint`, `Les`, `Aix`...), a trade / service noun (`Chauffage`, `Climatisation`, `Dépannage`...), a company word (`Premium`, `Groupe`...) or a manufacturer (the brands of `activity_rules`); a last name is never a non-surname noun (`Climatisation`, `Eau`, `Provence`, `Premium`...). Real surnames that are also nouns (`Charpentier`, `Boulanger`) stay allowed. This removes headings such as "Chauffage Climatisation", towns ("Saint Etienne") and products ("Daikin Premium", "Air Eau") that were returned as people.
- A line that credits someone who is not the company's own team is never read for name + function: hosting / web-agency / copyright credits, "écrit par" article authors, testimonials (quotes, "client", "avis"), partners, suppliers, sub-contractors. The legal-notice formula ("Directeur de la publication : ...") is still read.
- A civility ("Mme", "M.", "Dr") no longer swallows the first name ("Mme Claire Petit" is found). A rejected pair ("Directeur Marc") no longer hides the name that follows ("Directeur Marc Leroy" gives Marc Leroy, and a line with two names stays ambiguous: nobody).
- The stored function no longer contains the person's own name nor a trailing "chez / de / du".
- Known limits (to read in the sample): a function line between two names is attached to the name ABOVE it (the usual team-grid layout); two people on one line are skipped; the vocabulary is for French building trades; a name that looks like a person but is a product name is still possible.

**LinkedIn profile of a director** (`social_search`, network `linkedin_director`): attached to the person (`company_interlocutors.profile_url`) ONLY with a proof (SIREN on the page / snippet, link to the proven site, a phone or email we hold, address) AND both names on the page; otherwise the profile is a `to_verify` candidate in `social_searches` and the person keeps no `profile_url` (covered by a real-PostgreSQL test).

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

## Social accounts by web search (spec section 9)
```
python -m fbdb.enrich.social_search --limit 5                               # paid: 5 companies first, read the log
python -m fbdb.enrich.social_search --limit 50 --networks facebook,instagram # restrict the paid calls to some networks
```
Migration `sql/016_social_search.sql` (table `social_searches`, source `social_search`). NOT in `run_weekly.sh` (paid, manual). **NOT verified live**: written from the Firecrawl v1 search docs (`POST /v1/search`, answer `{"success", "data": [{"url","title","description"}]}`); the build sandbox has no Firecrawl key. Check with `--limit 5`: the search credit cost, whether `site:` operators (including `site:linkedin.com/company`) are honoured, and what the snippets contain.
- Order of truth: a link on the site proven by SIREN (`fbdb.enrich.website`) always comes first. The search only runs for networks the company has NO account on yet (facebook, instagram, tiktok, linkedin_company, youtube) and for each active director (max 2) without a LinkedIn profile.
- Queries: `site:facebook.com "Nom" "Ville"` (same for instagram.com, tiktok.com, youtube.com, `site:linkedin.com/company`); director: `site:linkedin.com/in "Prenom Nom" "Nom"`. Name = commercial name, else legal name without SARL/SAS...; town = head office. Only name and town go to the provider, never a phone or an email.
- Candidates are profile URLs only (posts, share links, hashtags, videos, personal-profile directories are dropped). Up to 3 per search are checked on the search snippet (free); if none is proven, the FIRST one is scraped (1 credit).
- **Proof (`evidence_type`)**: `siren_on_page` (our Luhn-valid SIREN), `website_linked` (the page links to the domain of our site proven by SIREN: a link, not a mention in the text), `phone_match` / `email_match` (a value we already hold, `sourced`/`verified`), `address_match` (postal code + city + every significant word of the head-office street). For a director's profile both first and last name must also be on the page.
- `confirmed` -> `company_contacts` (`sourced`, source `social_search`, `match_proof` = the passage, `match_url`, `last_verified_at`); the director's profile -> `company_interlocutors.profile_url` + `match_proof`. `to_verify` stays ONLY in `social_searches` (with the search result that proposed it): it is never a contact, never exported, and not counted as an account in the KPIs.
- Absence statuses (per network): `not_found` = the search ran and returned no profile of that network (NOT "no account"); `inaccessible` = the provider refused the query (retried after `--retry-days`, default 7); "non recherche" = no row.
- Memory / no double payment: one row per company x network (x director). Searched again only after `--refresh-days` (180). A search that did not run because the daily cap was reached is not recorded (retried tomorrow). Daily cap = `FIRECRAWL_DAILY_CAP`, shared with `website`. Worst case per company: 5 company networks + 2 directors x (1 search + 1 scrape) = 14 credits.
- No silent overwrite: a refresh that finds a DIFFERENT unproven candidate keeps the first one and writes the other to `field_conflicts` (`social:<network>`); a refresh that finds nothing never erases a candidate (only the date moves); a PROVEN account replaces a candidate and the displaced URL is recorded as the conflict.
- Known limits: Facebook / Instagram / LinkedIn pages are often behind a login wall, so many accounts will stay `to_verify` unless the snippet carries the proof; a phone or address printed on a page of a different business at the same place would still match. Before using it at scale, label ~100 results by hand and measure the wrong-confirmation rate. LinkedIn matching of directors is refined in the interlocutor-quality step (section 5).

## Activity read from the site (spec section 3)
```
python -m fbdb.enrich.site_activity --limit 5      # paid: 5 companies first, read the log
```
Migration `sql/017_site_activity.sql` (`company_activities`, `activity_runs`). NOT in `run_weekly.sh` (paid, manual). **NOT verified live**: same Firecrawl scrape call as `fbdb.enrich.website` (format and credit cost unverified, no key in the build sandbox). The keyword vocabulary (`fbdb/enrich/activity_rules.py`) is a first version for building trades and has **not been measured on real sites**: after `--limit 5`, label a sample (company, claim, correct yes/no) before anyone targets on it.
- Only a website contact that carries a SIREN proof (`match_proof`) is read. Per company: home page + up to 3 same-site pages that look like services / prestations / metiers / activites / depannage / entretien / a-propos (blog, news, hiring, legal, contact, FAQ, shop pages and files are skipped). At most 4 scrapes per company, inside `FIRECRAWL_DAILY_CAP` (shared with `website`); a company is not started unless 4 credits are left, so a run never stops in the middle of a site.
- Facets: `trade` (metier reel), `service` (installation, remplacement, mise aux normes, diagnostic, SAV, ramonage, desembouage, recherche de fuite), `specialty` (IRVE, climatisation, pompe a chaleur, photovoltaique, domotique, VMC, renovation energetique, poele/cheminee, chaudiere, traitement de l'eau, alarme/securite, courants faibles, salle de bain, isolation, eclairage), `work_type` (neuf / renovation / depannage / maintenance), `client_type` (particuliers, professionnels, tertiaire, industrie, collectivites, syndics/coproprietes, bailleurs sociaux, promoteurs/maitrise d'oeuvre), `contract` (contrat d'entretien, urgence/astreinte 24h-7j), `brand` (a manufacturer named on the page), `approval` (agrement / label CLAIMED on the site, and `brand_agreement:<brand>` when an agreement word sits in the same sentence as the brand).
- Every row = a claim made by the site: `passage` (the sentence or menu line that justifies it), `matched_text` (the words that fired the rule), `source_url`, `collected_at` / `last_seen_at`, `site_proof` (why that site is this company's). The first passage found is kept, home page first.
- **Photos are never a proof**: images and their alt text are removed before reading, so a gallery of charging points proves nothing and "experience" is never derived from an image.
- Not read as a claim: a negation right before the keyword ("nous ne faisons pas de climatisation", "plus de depannage"), recruitment sentences ("nous recrutons un electricien"), and for trade / service / specialty / work type a sentence about a partner or sub-contractor or about a training offer.
- A claimed approval (RGE, Qualibat, Qualifelec, QualiPAC...) is NOT an official certification: it stays in `company_activities` (facet `approval`) and never goes into `company_certifications` / `ademe_rge`. Comparing the two is part of the audit step.
- Absence statuses (`activity_runs.outcome`): `extracted` (at least one claim), `nothing_found` (pages read, no rule matched: NOT "does nothing" and NOT "never did such work"), `inaccessible` (home page unreadable, retried after `--retry-days`, default 7); no row = not read yet. A claim is never deleted because a later read no longer finds it (only `last_seen_at` moves). Re-read after `--refresh-days` (180).
- Not exported: the HubSpot company/contact exports are unchanged (the client has not asked for these fields in the import yet). The data is queryable in `company_activities`.
- Known limits: keyword rules cannot tell "we install heat pumps" from a menu entry of a page that no longer exists; "professionnels" / "commerces" are loose client-type hints; a site that names a brand in a logo bar is read as "brand named", not "authorised dealer" (only an agreement word in the same sentence gives `brand_agreement`).

## Public contracts from DECP (spec section 12, part 1/5)
```
python -m fbdb.enrich.decp decp.json --source-url https://www.data.gouv.fr/... --label "DECP consolidated 2026-10" \
    --coverage-note "period, thresholds, publishers" --rejects data/exports/decp_rejects.csv
python -m fbdb.enrich.decp decp.csv --source-url ... --map holder_id=Titulaire_SIRET     # CSV header override if needed
```
Migration `sql/018_public_contracts.sql` (`public_contracts`, `decp_loads`, view `v_public_contract_status`). Free (open data), not in `run_weekly.sh` (you download the file). **NOT verified live**: the build sandbox cannot reach data.gouv.fr (proxy 403). Field names (`id`, `uid`, `acheteur.id/nom`, `titulaires[].typeIdentifiant/id/denominationSociale`, `objet`, `montant`, `dateNotification`, `datePublicationDonnees`, `codeCPV`, `procedure`, `nature`, `dureeMois`, `lieuExecution`) and the CSV header aliases come from the DECP documentation: run a small real file first and read the stats and the rejects file.
- JSON (`{"marches": [...]}`, bare array, or legacy `{"marches": {"marche": [...]}}`) is streamed (a multi-GB file does not fill memory); a file without a markets array or truncated raises an error instead of loading nothing. CSV = one row per titulaire.
- A contract is attached to a company ONLY through the titulaire identifier as published: a Luhn-valid SIRET (type SIRET or no type) or SIREN. TVA / foreign identifiers and invalid numbers go to the rejects file (`INVALID_TITULAIRE_ID`), a market with no `uid` and no buyer+market id too (`NO_UID`). Only companies already in our base AND diffusible get a row; other titulaires are only counted (`rows_not_in_base`), nothing is created.
- Proof of every row: `decp_uid`, `market_id`, buyer id/name, and `source_url` (the dataset URL given at load, mandatory), plus the load (`decp_loads`: label, file, coverage note, counts, notification period).
- No silent overwrite: loading the same contract again is a `duplicate`; a different amount or notification date goes to `field_conflicts` (`decp:amount:<uid>`, `decp:notification_date:<uid>`) and the first value is kept. Modifications (avenants) are NOT read.
- `amount_suspect` = amount <= 0, >= 1e9, or too large to store (kept as published when storable, never summed). No other placeholder heuristic is applied (nothing unverified). An unknown amount stays NULL.
- Several titulaires on a market (group): one row per titulaire SIREN, `co_holders` says how many. A company with several SIRETs of the same SIREN has one row per market.
- **Absence**: "no contract" is never stored. `v_public_contract_status`: `found`, `none_in_loaded_datasets` (only "not in the files we loaded": DECP holds what buyers published, mostly above the legal threshold, with gaps and duplicates; it is NOT "never worked for the public sector"), `not_checked` (no DECP file loaded at all).
- Section 12, part 2 (realisations, partners, recruitment from the proven site) is below. Press is below (part 3/5, which completes section 12).

## Realisations, partners and recruitment from the site (spec section 12, part 2/5)
```
python -m fbdb.enrich.site_signals --limit 5      # paid: 5 companies first, read the log
```
Migration `sql/019_site_signals.sql` (`company_site_signals`, `signal_runs`). NOT in `run_weekly.sh` (paid, manual). **NOT verified live**: same Firecrawl scrape call as `fbdb.enrich.website` (format and credit cost unverified). The vocabulary (`fbdb/enrich/signal_rules.py`) is a first version for building trades and **not measured on real sites**: after `--limit 5`, label a sample (company, signal, correct yes/no) before anyone targets on it.
- Only a website contact with a SIREN proof (`match_proof`) is read. Per company: home page + up to 3 same-site pages, one page of each kind first (realisations / chantiers / references, then partners / clients / marques, then recruitment / careers), then the next best. Blog, news, legal, contact, FAQ, shop, gallery pages and files are skipped. At most 4 scrapes per company inside `FIRECRAWL_DAILY_CAP` (shared), a company is not started unless 4 credits are left. **Cost note**: the home page is scraped again (not cached), so with `site_activity` a company can cost up to 8 credits; merging both passes into one read is a possible saving.
- Kinds (`company_site_signals.kind`), every row a CLAIM of the site (`status = 'claimed'`, never "verified") with `passage`, `matched_text`, `source_url`, `site_proof`, `collected_at` / `last_seen_at`:
  - `realisation`: a sentence on a realisations / chantiers / references / projects page that names a kind of work AND something specific (a year, a surface or power, a postcode, a place, "pour <un client>"). `year_mentioned` only when the sentence writes a year. "Nous realisons tous types de travaux", navigation lines ("Voir toutes nos realisations") and short generic lines are not realisations. Max 10 per company.
  - `partner`: a NAME listed under a partner / client / reference / supplier heading, or after "nos partenaires :", "ils nous font confiance :", "en sous-traitance pour". `relation` is the word the site uses (`partner`, `client_reference`, `subcontracting`, `supplier_brand`): the direction of a relationship is never interpreted and the name is **never matched to a company of our base** (no SIREN guessed). A heading that is itself a link is a menu entry; a block that contains a menu word (Accueil, Contact, Realisations...) is a menu. Max 30 per company. Where the passage is a heading and a list item it is the two real lines joined by `>`.
  - `hiring`: "nous recrutons...", "offre d'emploi", "poste a pourvoir", "nous cherchons", and (on a recruitment page only) an "H/F" job line or "rejoignez-nous". `name` = role read from the sentence (trade or technicien, chef de chantier...), `relation` = contract word (cdi, cdd, alternance, interim, stage). Negations ("nous ne recrutons pas", "aucune offre d'emploi", "pas de poste") and "rejoignez-nous sur Facebook" are not signals. Max 5 roles per company. A recruitment claim means "seen recruiting on <date>", never "recruiting now": a later read that no longer finds it only leaves `last_seen_at` unchanged.
- **Photos and logo bars are never read**: images and alt text are removed before reading, so a gallery or a row of partner logos proves nothing.
- Absence statuses (`signal_runs.outcome`): `extracted`, `nothing_found` (pages read, no signal matched: NOT "no chantier" / "no partner" / "not recruiting"), `inaccessible` (home page unreadable, retried after `--retry-days`, default 7); no row = not read yet. Re-read after `--refresh-days` (90). Nothing is deleted on refresh; the first passage and URL are kept.
- Metrics: new KPI `match_companies_with_site_signals`. Not exported to HubSpot (data is queryable in `company_site_signals`).
- Known limits: keyword rules, no named-entity recognition (a capitalised word in a list can be a false partner; the `>` passage shows it); a realisation sentence is a claim, not proof the work was done by this company; a site that lists a partner does not say who works for whom.

## Press and web mentions (spec section 12, part 3/5)
```
python -m fbdb.enrich.press --limit 5      # paid: 5 companies first, read the log
```
Migration `sql/020_press.sql` (`company_press`, `press_searches`, source `press_search`). NOT in `run_weekly.sh` (paid, manual). **NOT verified live**: Firecrawl search and scrape (response shape, credit cost, what a search result contains) are unverified, as for `social_search`. After `--limit 5`, label ~100 results by hand (company, article, about this company yes/no) to measure the wrong-confirmation rate before anyone relies on it.
- Per company ONE search `"Nom" "Ville"` (commercial name, else legal name, without SARL / SAS...). Results dropped before anything else: the company's own site (proven by SIREN, subdomains included), social networks, company directories and registries (societe.com, pappers, infogreffe, pagesjaunes, annuaire-entreprises...), job boards, review / lead sites, certification bodies' own sites (`NOT_PRESS` in `fbdb/enrich/press.py`: extend it after the first sample) and any result whose title / snippet does not write the company name as whole words. At most 5 candidates are kept.
- **Homonyms**: a name + a town is not a proof. An article is `confirmed` only with the same proofs as the social accounts (`fbdb.enrich.social_search.find_evidence`): `siren_on_page`, `website_linked` (the page links to the site proven by SIREN), `phone_match` / `email_match` (one we already hold; nothing is sent to the provider), `address_match` (street words + postal code + city). Proof is looked for in the snippet first (free) and then on the page of the first 2 candidates without one (1 scrape each). Otherwise `to_verify`: stored, never exported, never counted as press of the company.
- Stored per article: URL, publisher host, title, `mention_passage` (the line of the snippet / page that writes the name), status, proof. **Nothing is interpreted**: no date, topic or sentiment is guessed (a search result does not give a reliable publication date, so none is stored), and the article is not summarised.
- Absence statuses (`press_searches.status`): `confirmed`, `to_verify`, `not_found` (search ran, no usable mention: NOT "never in the press"), `inaccessible` (provider refused the query, retried after `--retry-days`, default 7); no row = not searched yet. The row is the memory: nobody is paid twice before `--refresh-days` (180). A later search never deletes an article; a `to_verify` article that a later search proves is upgraded, a `confirmed` one is never downgraded; the status of a company is derived from what is stored (a refresh that finds less does not lower it). An `inaccessible` answer is recorded as such and the stored articles stay.
- Cost: worst case 1 search + 2 scrapes = 3 credits per company, inside `FIRECRAWL_DAILY_CAP` (shared); a company is not started unless 3 credits are left.
- Metrics: `match_companies_with_press` (at least one confirmed article) and `match_companies_with_press_to_verify` (candidates, none confirmed). Not exported to HubSpot.
- Known limits: a paywalled article gives no proof and stays `to_verify`; a very common name returns many homonyms (they stay `to_verify`); a press release published by the company on a news site is a mention, not independent coverage (the module does not judge); regional press domains are not whitelisted (everything outside `NOT_PRESS` counts as a third-party page).
- Section 12 is now complete in code (DECP contracts, realisations, partners, recruitment, press); none of it has been run against live data.

## Audit (step 6) - fixes found by reading the code against the spec
The client's French document is not in the repository: the audit compares the code with the requirements as they were relayed (SIRENE, geocoding, Pappers financials per exercise, Google Places limits, RGE / Qualibat / Qualifelec / RC pro). Each fix is a separate commit.

### 6a. Financials per exercise (Pappers, spec section 4)
Migration `sql/003b_financials_exercice.sql` (numbered 003b, not 021: `004_export_view.sql` reads its columns and all files run in order at every start). Found and fixed:
- Rows were keyed by calendar year only, with no closing date or length: a 6 or 18 month first exercise looked like a year. Now `period_end` and `duration_months` are stored when Pappers publishes them (never guessed; unreadable value = NULL); two exercises ending in the same year no longer merge. The HubSpot view uses the latest exercise of 12 months or of unknown length, so a short exercise is never sent as "annual revenue".
- A re-read silently overwrote the stored figures. Now the first value is kept, a different one goes to `field_conflicts` (`financials:<closing date>:revenue_eur|net_result_eur|employees`), and a figure we did not have yet is filled in.
- A year with only a net result was dropped; it is now stored.
- No search status: only a date. New table `financials_checks` (`found` / `not_found` / `inaccessible`; no row = not searched). `not_found` is "the provider has no accounts for it", not "no revenue" (`detail` says which); `inaccessible` is retried on the next run and erases nothing.
- Provenance: `source_url` is the API call (without token) next to the provider and `collected_at`.
- **NOT verified live**: `date_cloture_exercice` and `duree_exercice` come from the Pappers documentation, not from a real response. After `python -m fbdb.enrich.pappers --limit 5`, check in `company_financials` that `period_end` / `duration_months` are filled; if they are NULL the field names differ.

### 6b. Google Places (spec section 10: reviews)
Migration `sql/021_places_status.sql`. Found and fixed:
- "No rating" had no reason. `company_reviews.status` is now `matched` (confidence >= 0.8, the only rows the export uses) / `to_verify` (a candidate exists, not proven, never exported) / `not_found` (search ran and returned nothing: NOT "no Google page") / `inaccessible` (call failed); no row = not searched. Old rows are backfilled from their confidence.
- A failed call recorded nothing, so it looked like "not searched" and was retried at every run. It now writes `inaccessible` with `error_detail` and is asked again after `PLACES_RETRY_DAYS` (default 7). A rating already stored is never touched by a failed call. A billing / key error (`QuotaExhausted`) is not recorded against the establishment.
- Still open (client decision, not changed): Google's terms give no caching exception for rating / review count, the refresh is 30 days, but the HubSpot view does not check the age of a stored rating. If the licence is not agreed, `--allow-google-ratings` must stay off. The 0.8 threshold and the 0.6 / 0.4 weights are not measured on real establishments: label ~50 `to_verify` / `matched` rows by hand after the first `--limit 20`.

### 6c. Geocoding (spec section 2)
`sql/007_geocoding.sql` (extended, idempotent). Found and fixed:
- **Stale coordinates**: when the weekly SIRENE load changed the street / postcode / city of an establishment, the old latitude / longitude / standardized address stayed with `address_status='verified'`, and the Places matching measured distances from them. A trigger (`trg_reset_geocode_on_address_change`) now clears them and puts the row back to "not geocoded"; the next `fbdb.enrich.geocode` run takes it. A geocoder write (address unchanged) and rows we never geocoded (`sourced`) are not touched.
- **Town centre taken for the address**: a match that only found the town (`result_type = municipality`, score can still be 0.8+) was stored as `verified`. It is now flagged (`MUNICIPALITY_ONLY` in `data_quality_issues`), no coordinates are stored, and `geocode_precision` keeps what the service answered (housenumber / street / locality / municipality). When the service does not return `result_type` the old rule (score >= 0.6) applies.
- **NOT verified live**: `result_type` is requested as a result column from the BAN CSV documentation; check on the first run that `geocode_precision` is filled. The 0.6 score threshold is not measured: after the first run, look at ~50 rows around 0.6-0.8 by hand (`standardized_address` against the SIRENE address).

### 6d. Certifications and insurance (spec section 11: RGE, Qualibat, Qualifelec, RC pro)
Migration `sql/022_certification_status.sql`. There was no PostgreSQL test for the licensed-file loader at all; there is now. Found and fixed:
- **No trace of which file a row came from**: the contract reference was only in a batch note. New table `certification_loads` (provider, contract, file, coverage note, types the file carried, counts) for both `fbdb.enrich.certifications` and `fbdb.enrich.ademe_rge`; each new row has `load_id`. Rows loaded earlier get a `backfill` load (file and date unknown, said so). New CLI options `--provider` and `--coverage-note`.
- **A changed end date was dropped silently**: a known certificate re-read with another `valid_to` was just "already known". The first value is kept and the other goes to `field_conflicts` (`certification:<type>:<label>:<valid_from>:valid_to`); no duplicate conflict on re-runs.
- **"Not in the file" looked like "not certified"**: view `v_certification_status` per company and per type that a loaded file carried: `found_valid` / `found_not_current` (all expired) / `none_in_loaded_files` (NOT "not certified": only "not in the files we loaded"); a type no file ever carried has no row = not checked.
- `last_seen_at` on each row: the last load that listed it. RC pro / décennale rows had no source; they now use source `certification_file`.
- **Still open (client decision, not changed)**: ADEME rows of an establishment that is absent from a LATER full ADEME file are kept (only establishments present in the file are replaced), so a withdrawn RGE with a future end date keeps being exported until its `valid_to`. `last_seen_at` shows it; the rule "export only rows seen in the latest full file" is not applied because a partial file would then hide every other RGE.
- Not verified: no real Qualibat / Qualifelec / RC pro file has been loaded (they need a licence); the CSV columns are the ones the loader expects, remapped with `--mapping`.

### 6e. SIRENE (spec section 1): diffusion of the establishment
`sql/003c_address_diffusion.sql` (003c: the export view reads it). Found and fixed:
- Only the COMPANY's diffusion status was stored. The loaders skip establishments INSEE does not diffuse (`statutDiffusionEtablissement` P / N, fields masked `[ND]`), so an establishment that WAS diffusible and later was not kept its old address and stayed in the HubSpot export and in provider queries. `company_addresses.is_diffusible` is now synced at every load (bulk and weekly); the view requires company AND establishment; Places, geocoding, website discovery, social search and press skip a non-diffusible establishment (a company whose head office is not diffusible is not searched by those). Nothing is deleted; diffused again, the flag is cleared and the fields are refreshed in the same run.
- Still open: the weekly history (`company_change_history`) does not record the diffusion change itself, and contacts / people already stored for the company are not removed when an establishment stops being diffused (decision for the client / DPO). SIRENE API quota (30 / min), delta by `dateDernierTraitement`, closure dates and sole-proprietor handling were read and left as they are (tests cover them); the API field names and quota are not re-verified from this sandbox.

## Step 7: phone / email conflicts (small items)
- `fbdb.match.resolve.contact_conflict()` now feeds `persist_conflicts()` from the two loaders that write phones and emails (`licensed_contacts.store_contact` and `website._put_contact`). When a value from another source differs from the company's primary phone / email of the same establishment, the new value is kept as a contact AND recorded in `field_conflicts` (`field_name` `phone` or `email`, `kept_value` = the primary, `other_value` = the new one, both sources). The primary is never changed by it: the exported value stays the first one until a human decides.
- Not a conflict: same source listing two numbers (a site with a landline and a mobile), the same value read again, or no primary yet. Re-runs add no duplicate row (UNIQUE key of `field_conflicts`).
- Not done on purpose: the opposition **lift** tool (legal decision, client / DPO). `email_find` stores into its own table and was not touched.
- Tests: unit (fake cursor) in `tests/test_resolve.py`, PostgreSQL in `tests/integration/test_contact_conflicts_pg.py`.

## Spec gap fixes (after reading the client's French document)
The client's document is now available; each gap found against it is a separate commit.

### G1. Balance-sheet figures (spec section 4)
Migration `sql/023_financials_balance.sql`: `equity_eur` (capitaux propres), `cash_eur` (tresorerie), `debt_eur` (endettement) per exercise in `company_financials`, filled by `fbdb.enrich.pappers`. A figure Pappers does not publish stays NULL (never 0; a published 0 stays 0). Same rules as revenue: first value kept, a different one goes to `field_conflicts` (`financials:<closing date>:equity_eur|cash_eur|debt_eur`), a figure we did not have yet is filled in; an exercise with only balance figures is stored. **NOT verified live**: the Pappers field names are tolerant aliases (`capitaux_propres` / `fonds_propres`, `tresorerie` / `disponibilites`, `endettement` / `dettes_financieres`); after `python -m fbdb.enrich.pappers --limit 5` check in `company_financials` that these columns are filled, else adjust `BALANCE_ALIASES`. Not exported to HubSpot (the client has not asked for these columns).

### G2a. Building types and commitments (spec section 3)
Migration `sql/024_activity_facets_more.sql` adds two facets to `company_activities` (same table, same rules, no extra Firecrawl cost: they are read from the pages `site_activity` already reads):
- `building_type`: maison individuelle, immeuble / copropriete, appartement, commerce, bureaux, industrie / entrepot, ERP, hotel / restaurant, agricole, batiment public (ecole, sante). A claim of the text, never a photo; "nous ne travaillons pas sur les commerces" is not a claim; hiring sentences are skipped.
- `commitment` (positionnement et engagements): delai annonce (sous 48h, intervention rapide), devis gratuit, garantie, accompagnement / suivi, specialisation. Each one keeps its passage. A promise on the site is what the company SAYS, not a verified fact (a "garantie" claim is not an insurance proof: that stays in section 11).
- Vocabulary is a first version, NOT measured on real sites: label a sample after `python -m fbdb.enrich.site_activity --limit 5`. `garantie` is a loose word (also matches "garantie constructeur"), the passage lets a person judge.

### G2b. Declared service area, mobility and team size (spec sections 2 and 4)
Migration `sql/025_site_declarations.sql` (`company_site_declarations`) and `fbdb.enrich.declaration_rules`. Read from the pages `site_activity` already reads (same run, same Firecrawl credits, no extra scrape). Each row is what the SITE says, stored as written with its passage, page URL and the SIREN proof of the site:
- `service_area_place`: town / department / region named after "nous intervenons a ...", "zone d'intervention : ...", "nous nous deplacons a ...". Only proper names (capital letter or a department number) are kept; `value` is the name as written ("Rhone (69)"). **No INSEE code and no distance is computed**: the address of the company (SIRENE) and its service area stay two different things, and nothing is written into `company_addresses`.
- `service_area_radius_km`: "rayon de 30 km autour de Rennes" (`number` = 30).
- `service_area_scope`: `national` ("partout en France", "toute la France") or `regional` ("toute la region"), ONLY when the sentence also talks about working / moving / the area (so "fabrique en France" is not mobility).
- `team_size`: "une equipe de 12 personnes", "plus de 15 techniciens" (`number`, `qualifier` exact / plus_de / environ / pres_de). One per read. Kept APART from the official headcount (`company_financials.employees`, SIRENE bracket): never merged, never used to fill them. A different size on a later read is a new row (history), nothing is overwritten.
- Not read: images / alt text, recruitment sentences, negations ("nous n'intervenons pas a Paris", "hors de ..."), sub-contractor / partner sentences, "vos salaries" (the client's staff), "plus de 20 ans d'experience".
- Known limits: a list written as bullet lines under a "Zone d'intervention" heading is not read (one sentence or line only); "Hauts-de-France" style names are kept as written; the rules are NOT measured on real sites (label a sample after `site_activity --limit 5`).
- Absence: a company with no row = nothing declared / found, NOT "works nowhere" or "team size unknown to the company". Metrics: `match_companies_with_declared_service_area`. Not exported to HubSpot.

## First test lot: what to measure (client's last paragraph)
`python -m fbdb.report.metrics` now reports (report only, no target agreed): `match_companies_with_proven_website`, `match_companies_with_interlocutor`, `match_companies_with_social_account` (confirmed accounts only), `match_companies_with_social_to_verify` (candidates found by search, no proof, no confirmed account), `match_companies_with_activity_claims` (at least one claim read from the proven site), `match_companies_with_public_contracts` (at least one DECP contract in the loaded files), `match_companies_with_press` / `match_companies_with_press_to_verify` (press confirmed with a proof / candidates only), `match_companies_with_site_signals` (at least one realisation / partner / recruitment claim read from the proven site), `match_directors_with_mobile_linked` (a +336/+337 number attached to the director with evidence, not merely held by the company), `match_directors_with_verified_email_linked`, `match_directors_with_any_email_linked` (includes provider hypotheses), `match_directors_decision_maker_confirmed`.
Suggested order for the test lot of ~100 companies (all paid steps start with `--limit 5`, read the log, then widen):
1. `python -m fbdb.enrich.directors` (free), then `python -m fbdb.enrich.interlocutors sync-directors`
2. `python -m fbdb.enrich.website` (or `website_discovery` for companies with no site) -> proven site, socials, phones, emails, people
   then `python -m fbdb.enrich.social_search --limit 5` for the networks the site did not link
3. `python -m fbdb.enrich.email_find --limit 5` -> `python -m fbdb.enrich.email_verify` -> `python -m fbdb.enrich.phone_lookup`
4. `python -m fbdb.report.metrics`, then export (`fbdb.export.run_export`, `fbdb.export.hubspot_contacts`) and a 100-row HubSpot test import.
