-- Review platforms found by the same search as the social accounts (client spec section 10: other review platforms). Idempotent.
-- Same statuses and proof rules as 016_social_search.sql: 'confirmed' only with a proof, otherwise 'to_verify'; not_found is NOT
-- "no page". Only the LINK of the page is kept: no review text, no rating, no company reply (the platforms' terms are not ours to decide).
ALTER TABLE social_searches DROP CONSTRAINT IF EXISTS social_searches_network_check;
ALTER TABLE social_searches ADD CONSTRAINT social_searches_network_check CHECK (network IN
  ('facebook','instagram','tiktok','linkedin_company','linkedin_director','youtube','trustpilot','pagesjaunes'));
