-- Paid enrichment bookkeeping (Chunk 4). Idempotent.
-- api_usage: one row per provider and UTC day; the daily cap is enforced atomically by fbdb.common.usage.reserve().
CREATE TABLE IF NOT EXISTS api_usage (
  usage_date DATE NOT NULL DEFAULT (now() AT TIME ZONE 'utc')::date,
  provider   TEXT NOT NULL,
  calls      INTEGER NOT NULL DEFAULT 0,
  credits    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (usage_date, provider)
);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS financials_checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_companies_fin_checked ON companies(financials_checked_at NULLS FIRST);
