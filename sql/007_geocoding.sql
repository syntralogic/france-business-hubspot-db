-- Geocoding bookkeeping (Chunk 2). Idempotent.
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS geocoded_at   TIMESTAMPTZ;
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS geocode_score NUMERIC(4,3);
CREATE INDEX IF NOT EXISTS ix_addr_to_geocode ON company_addresses(establishment_id) WHERE geocoded_at IS NULL;

-- Precision of the match (BAN result_type: housenumber / street / locality / municipality). NULL = not given.
ALTER TABLE company_addresses ADD COLUMN IF NOT EXISTS geocode_precision TEXT;

-- When SIRENE changes the street / complement / postcode / city of an establishment, the coordinates, the standardized
-- address and the "verified" status that were computed for the OLD address must not survive: the Places matching measures
-- distances from them. The row goes back to "not geocoded" and the next geocode run takes it. A write that changes the
-- address AND the coordinates together (a geocoder run) is left alone. Idempotent.
CREATE OR REPLACE FUNCTION fn_reset_geocode_on_address_change() RETURNS trigger AS $$
BEGIN
  IF (NEW.street, NEW.complement, NEW.postal_code, NEW.city) IS DISTINCT FROM (OLD.street, OLD.complement, OLD.postal_code, OLD.city)
     AND (NEW.latitude, NEW.longitude, NEW.geocoded_at) IS NOT DISTINCT FROM (OLD.latitude, OLD.longitude, OLD.geocoded_at)
     AND (OLD.address_status = 'verified' OR OLD.geocoded_at IS NOT NULL) THEN
    NEW.latitude := NULL;
    NEW.longitude := NULL;
    NEW.standardized_address := NULL;
    NEW.geocode_score := NULL;
    NEW.geocode_precision := NULL;
    NEW.geocoded_at := NULL;
    IF OLD.address_status = 'verified' THEN
      NEW.address_status := 'unverified';
    END IF;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reset_geocode_on_address_change ON company_addresses;
CREATE TRIGGER trg_reset_geocode_on_address_change BEFORE UPDATE ON company_addresses
  FOR EACH ROW EXECUTE FUNCTION fn_reset_geocode_on_address_change();
