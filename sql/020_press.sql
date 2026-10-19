-- Press / third-party web mentions found by web search (client spec, section 12, part 3). Idempotent.
-- A name + a town is NOT a proof (homonyms): an article is `confirmed` only with a proof that the page is about THIS company
-- (its SIREN, a link to its site proven by SIREN, a phone / email we already hold, or its full address); otherwise `to_verify`,
-- never exported and never counted as press of the company.
CREATE TABLE IF NOT EXISTS company_press (
  press_id         BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  article_url      TEXT NOT NULL,
  publisher        TEXT,                    -- host of the page, as returned by the search (no judgement on its quality)
  title            TEXT,
  mention_passage  TEXT NOT NULL,           -- the line of the search result / page where the company name is written
  status           TEXT NOT NULL CHECK (status IN ('confirmed','to_verify')),
  evidence_type    TEXT CHECK (evidence_type IN ('siren_on_page','website_linked','phone_match','email_match','address_match')),
  proof            TEXT,                    -- confirmed: the passage that proves it is this company; to_verify: NULL
  query_text       TEXT,
  source_id        SMALLINT REFERENCES data_sources(source_id),
  collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status <> 'confirmed' OR (evidence_type IS NOT NULL AND proof IS NOT NULL)),
  UNIQUE (company_id, article_url)
);
CREATE INDEX IF NOT EXISTS ix_company_press_status ON company_press (status);

-- Memory of the search + per-company status. No row = not searched yet (non recherche).
--   confirmed     at least one article proven to be about this company
--   to_verify     mentions found, none proven: a human decides
--   not_found     the search ran and returned no usable mention     (NOT "the company never appeared in the press")
--   inaccessible  the provider refused the query                    (retried after a short delay)
CREATE TABLE IF NOT EXISTS press_searches (
  company_id   BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  searched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  query_text   TEXT,
  status       TEXT NOT NULL CHECK (status IN ('confirmed','to_verify','not_found','inaccessible')),
  hits_seen    SMALLINT NOT NULL DEFAULT 0,
  articles     SMALLINT NOT NULL DEFAULT 0
);

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('press_search','Press / web mention found by web search (Firecrawl)',5,FALSE,'on demand','URL, title and the line mentioning the company only; confirmed only with a proof, otherwise stays to_verify')
ON CONFLICT (code) DO NOTHING;
