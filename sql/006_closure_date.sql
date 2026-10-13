-- Upgrade for databases created before closure dates were tracked. Idempotent; a no-op on a fresh install
-- (001/002 already contain these columns).
ALTER TABLE companies         ADD COLUMN IF NOT EXISTS closure_date_basis TEXT
  CHECK (closure_date_basis IN ('insee','observed'));
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS closure_date_basis TEXT
  CHECK (closure_date_basis IN ('insee','observed'));
ALTER TABLE stg_sirene_etab   ADD COLUMN IF NOT EXISTS closure_date DATE;
