-- Directors enrichment bookkeeping (Annuaire des Entreprises).
-- Tracks when each company's directors were last checked so that companies WITHOUT any director
-- (most sole proprietors, some micro-companies) are not re-queried every week.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS directors_checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_companies_directors_checked ON companies(directors_checked_at NULLS FIRST);
