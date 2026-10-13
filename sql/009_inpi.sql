-- INPI RNE cross-check bookkeeping (Chunk 2). Idempotent.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS inpi_checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_companies_inpi_checked ON companies(inpi_checked_at NULLS FIRST);
