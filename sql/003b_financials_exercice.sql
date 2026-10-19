-- Financials per EXERCISE (spec section 4), not per calendar year.
-- Numbered 003b (not 021) on purpose: 004_export_view.sql reads these columns and every file is re-run at each start, in order.
-- Idempotent.

ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS period_end      DATE;      -- closing date of the exercise, as published
ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS duration_months SMALLINT;  -- length of the exercise; NULL = not published
ALTER TABLE company_financials ADD COLUMN IF NOT EXISTS source_url      TEXT;      -- the provider call this row came from (no token)

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_financials_duration') THEN
    ALTER TABLE company_financials ADD CONSTRAINT ck_financials_duration CHECK (duration_months IS NULL OR duration_months BETWEEN 1 AND 36);
  END IF;
END $$;

-- A company that changes its closing date can have two exercises ending in the same year: the old
-- (company, fiscal_year, source) key would silently merge them.
ALTER TABLE company_financials DROP CONSTRAINT IF EXISTS company_financials_company_id_fiscal_year_source_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_financials_exercice
  ON company_financials (company_id, source_id, fiscal_year, COALESCE(period_end, DATE '1900-01-01'));

-- Search status per company. No row = "non recherche". 'not_found' means "the provider has no accounts for it": it does
-- NOT mean the company publishes none or has no revenue. 'inaccessible' = the call failed, retried on the next run.
CREATE TABLE IF NOT EXISTS financials_checks (
  company_id      BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  status          TEXT NOT NULL CHECK (status IN ('found', 'not_found', 'inaccessible')),
  detail          TEXT,
  exercises_found SMALLINT,
  source_id       SMALLINT REFERENCES data_sources(source_id),
  checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
