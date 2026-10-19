-- Search status per establishment for Google Places (spec section 11): the row already says "found"; it must also say
-- WHY there is no rating. Idempotent.
--   matched       candidate accepted (confidence >= 0.8): the only rows the export may use
--   to_verify     a candidate exists but name / distance do not prove it is this establishment: kept, never exported
--   not_found     the search ran and returned no candidate (NOT "the company has no Google page")
--   inaccessible  the call failed; retried after PLACES_RETRY_DAYS (default 7); a rating already stored is kept as it was
--   no row        not searched yet
ALTER TABLE company_reviews ADD COLUMN IF NOT EXISTS status          TEXT;
ALTER TABLE company_reviews ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;
ALTER TABLE company_reviews ADD COLUMN IF NOT EXISTS error_detail    TEXT;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_reviews_status') THEN
    ALTER TABLE company_reviews ADD CONSTRAINT ck_reviews_status
      CHECK (status IS NULL OR status IN ('matched', 'to_verify', 'not_found', 'inaccessible'));
  END IF;
END $$;

UPDATE company_reviews SET status = CASE WHEN match_confidence >= 0.8 THEN 'matched'
                                         WHEN provider_place_id IS NOT NULL THEN 'to_verify' ELSE 'not_found' END
WHERE status IS NULL;
