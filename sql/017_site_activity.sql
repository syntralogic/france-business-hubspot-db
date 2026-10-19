-- What the company says it does, read from the pages of its OWN site proven by SIREN (client spec, section 3). Idempotent.
-- Every row is a CLAIM made by the site, with the passage that justifies it, the page URL and the date. Nothing is deduced from
-- photos, and the claimed approvals (RGE, Qualibat...) are NOT official certifications: those stay in company_certifications.
CREATE TABLE IF NOT EXISTS company_activities (
  activity_id   BIGSERIAL PRIMARY KEY,
  company_id    BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  facet         TEXT NOT NULL CHECK (facet IN ('trade','service','specialty','work_type','client_type','contract','brand','approval')),
  code          TEXT NOT NULL CHECK (code <> ''),
  label         TEXT NOT NULL,
  matched_text  TEXT NOT NULL,         -- the words of the page that triggered the rule
  passage       TEXT NOT NULL,         -- the sentence / line that justifies the classification
  source_url    TEXT NOT NULL,         -- page where it is written
  site_proof    TEXT,                  -- why that site is this company's (the SIREN proof of the website contact)
  source_id     SMALLINT REFERENCES data_sources(source_id),
  collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),   -- last run that still found it; a value is never deleted because it disappeared
  UNIQUE (company_id, facet, code)
);
CREATE INDEX IF NOT EXISTS ix_activities_facet_code ON company_activities (facet, code);

-- Memory of the reads, and the per-company outcome. No row = not read yet (non recherche).
--   extracted      pages read, at least one claim stored
--   nothing_found  pages read, no claim matched   (NOT "the company does nothing" / "never did such work")
--   inaccessible   the home page could not be read (retried after a short delay)
CREATE TABLE IF NOT EXISTS activity_runs (
  company_id  BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  searched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  pages_read  SMALLINT NOT NULL DEFAULT 0,
  outcome     TEXT NOT NULL CHECK (outcome IN ('extracted','nothing_found','inaccessible')),
  items_found SMALLINT NOT NULL DEFAULT 0
);
