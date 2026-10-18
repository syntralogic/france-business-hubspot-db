-- Social accounts found by web search when the company's own site does not link them (client spec, section 9). Idempotent.
-- One row per company x network (x person for the director's LinkedIn). The row is the MEMORY of the search (nobody is paid twice
-- before the refresh delay) and the per-network status:
--   confirmed     account found AND proven (SIREN / link to the proven site / known phone, email / address on the page or search snippet)
--   to_verify     candidate found, no proof: a human decides; never exported as a contact
--   not_found     the search ran and returned no profile of that network   (NOT "the company has no account")
--   inaccessible  the search provider refused the query                    (retried after a short delay)
-- "Not searched yet" (non recherche) is the ABSENCE of a row.
CREATE TABLE IF NOT EXISTS social_searches (
  search_id        BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  network          TEXT NOT NULL CHECK (network IN ('facebook','instagram','tiktok','linkedin_company','linkedin_director','youtube')),
  interlocutor_id  BIGINT REFERENCES company_interlocutors(interlocutor_id) ON DELETE CASCADE,   -- only for linkedin_director
  searched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  query_text       TEXT,
  status           TEXT NOT NULL CHECK (status IN ('confirmed','to_verify','not_found','inaccessible')),
  candidate_url    TEXT,
  evidence_type    TEXT CHECK (evidence_type IN ('siren_on_page','website_linked','phone_match','email_match','address_match')),
  proof            TEXT,          -- confirmed: the passage that proves it; to_verify: the search result that proposed the candidate
  proof_url        TEXT,
  last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((network = 'linkedin_director') = (interlocutor_id IS NOT NULL)),
  CHECK (status NOT IN ('not_found','inaccessible') OR candidate_url IS NULL),
  CHECK (status NOT IN ('confirmed','to_verify') OR candidate_url IS NOT NULL),
  CHECK (status <> 'confirmed' OR (evidence_type IS NOT NULL AND proof IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_social_search_subject
  ON social_searches (company_id, network, (coalesce(interlocutor_id, 0)));
CREATE INDEX IF NOT EXISTS ix_social_search_status ON social_searches (status, searched_at);

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('social_search','Social profile found by web search (Firecrawl)',5,FALSE,'on demand','Public profile URL only; confirmed only with a proof, otherwise stays a candidate')
ON CONFLICT (code) DO NOTHING;
