-- Realisations, partners and recruitment read from the pages of the company's OWN site proven by SIREN (client spec, section 12, part 2). Idempotent.
-- Every row is a CLAIM written on the site, with the passage, the page URL and the date. Nothing is deduced from photos or logo bars
-- (images and their alt text are removed before reading). A partner is NOT linked to a company of our base: no SIREN is guessed.
--   realisation : a described project / chantier (the sentence is kept; year_mentioned only when the sentence writes a year)
--   partner     : a name listed under a partner / client / reference heading or sentence; `relation` is the WORD the site uses
--                 (partner, client_reference, subcontracting, supplier_brand): the direction of the relationship is never interpreted
--   hiring      : the site says it recruits (sentence or job posting); `name` = role read from the sentence, `relation` = contract type
CREATE TABLE IF NOT EXISTS company_site_signals (
  signal_id      BIGSERIAL PRIMARY KEY,
  company_id     BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  kind           TEXT NOT NULL CHECK (kind IN ('realisation','partner','hiring')),
  code           TEXT NOT NULL CHECK (code <> ''),     -- dedup key inside (company, kind): hash of the passage, folded name, role
  name           TEXT,                                 -- partner name as written / role as written
  relation       TEXT,                                 -- see above
  year_mentioned SMALLINT CHECK (year_mentioned IS NULL OR year_mentioned BETWEEN 1900 AND 2100),
  matched_text   TEXT NOT NULL,
  passage        TEXT NOT NULL,
  source_url     TEXT NOT NULL,
  site_proof     TEXT,
  source_id      SMALLINT REFERENCES data_sources(source_id),
  status         TEXT NOT NULL DEFAULT 'claimed' CHECK (status IN ('claimed')),   -- a site claim, never "verified"
  collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),   -- a claim is never deleted because a later read no longer finds it
  UNIQUE (company_id, kind, code)
);
CREATE INDEX IF NOT EXISTS ix_site_signals_kind ON company_site_signals (kind);

-- Memory + outcome per company. No row = not read yet (non recherche).
--   extracted      pages read, at least one signal stored
--   nothing_found  pages read, none matched (NOT "no realisation" / "no partner" / "not hiring")
--   inaccessible   the home page could not be read (retried after a short delay)
CREATE TABLE IF NOT EXISTS signal_runs (
  company_id  BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  searched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  pages_read  SMALLINT NOT NULL DEFAULT 0,
  outcome     TEXT NOT NULL CHECK (outcome IN ('extracted','nothing_found','inaccessible')),
  items_found SMALLINT NOT NULL DEFAULT 0
);
