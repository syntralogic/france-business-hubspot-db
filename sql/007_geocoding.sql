-- Geocoding bookkeeping (Chunk 2). Idempotent.
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS geocoded_at   TIMESTAMPTZ;
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS geocode_score NUMERIC(4,3);
CREATE INDEX IF NOT EXISTS ix_addr_to_geocode ON company_addresses(establishment_id) WHERE geocoded_at IS NULL;
