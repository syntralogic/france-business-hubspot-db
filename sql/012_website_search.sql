-- Website discovery by web search: one row per company searched, so the same company is not searched (and paid for) every run.
-- 'not_proven' means: candidates were found but none showed our SIREN. It does NOT mean the company has no website. Idempotent.
CREATE TABLE IF NOT EXISTS website_searches (
  company_id         BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  searched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  query_text         TEXT,
  candidates_checked SMALLINT NOT NULL DEFAULT 0,
  outcome            TEXT NOT NULL CHECK (outcome IN ('proven','not_proven','no_results'))
);
