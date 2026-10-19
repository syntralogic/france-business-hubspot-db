-- Mailbox function ("fonction de la boite") and priority email (spec section 7). Idempotent.
-- Named 003d so it runs BEFORE 004_export_view.sql (the view reads contact_use and email_priority_rules).

-- Function of the mailbox / line. 013_interlocutors.sql adds the same column with IF NOT EXISTS (no-op once this ran).
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS contact_use TEXT
  CHECK (contact_use IN ('switchboard','direction','sales','emergency','after_sales','accounting','recruitment','general'));
-- HOW the function was decided. NULL = function not known, or set before this column existed (basis not recorded).
--   address_name  : read from the address itself (devis@, sav@, direction@ ...)
--   page_context  : read from the words around the value on the proven site
--   manual        : a person set it (python -m fbdb.enrich.email_priority set-use)
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS contact_use_basis TEXT
  CHECK (contact_use_basis IN ('address_name','page_context','manual'));
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_contact_use_basis_needs_use') THEN
    ALTER TABLE company_contacts ADD CONSTRAINT ck_contact_use_basis_needs_use
      CHECK (contact_use_basis IS NULL OR contact_use IS NOT NULL);
  END IF;
END $$;

-- Which mailbox function is preferred for prospecting. DATA, not code: the client edits it
-- (python -m fbdb.enrich.email_priority set-rule ...). Defaults below are an ASSUMPTION, not the client's decision.
-- 'unknown' = an email whose function we could not read (e.g. jean.dupont@...). Rows are never overwritten at restart.
CREATE TABLE IF NOT EXISTS email_priority_rules (
  contact_use   TEXT PRIMARY KEY CHECK (contact_use IN ('direction','sales','general','switchboard','unknown',
                                                         'after_sales','emergency','accounting','recruitment')),
  priority_rank SMALLINT NOT NULL CHECK (priority_rank BETWEEN 1 AND 99),   -- 1 = best
  eligible      BOOLEAN NOT NULL,                                          -- FALSE = never chosen as THE priority email
  note          TEXT
);
INSERT INTO email_priority_rules (contact_use, priority_rank, eligible, note) VALUES
  ('direction',   1, TRUE,  'default: the decision maker first'),
  ('sales',       2, TRUE,  'default: devis / commercial'),
  ('general',     3, TRUE,  'default: contact@ / info@'),
  ('switchboard', 4, TRUE,  'default'),
  ('unknown',     5, TRUE,  'default: function not readable (often a named mailbox)'),
  ('after_sales', 6, FALSE, 'default: customer service is not a prospecting mailbox'),
  ('emergency',   7, FALSE, 'default: breakdown / urgent line'),
  ('accounting',  8, FALSE, 'default'),
  ('recruitment', 9, FALSE, 'default')
ON CONFLICT (contact_use) DO NOTHING;

-- THE priority email of each company: best usable, eligible email by the rules above, then verified before sourced, then primary,
-- then most recently verified. Same safety filters as the HubSpot view: never 'inferred' / 'unavailable', nothing opposed on the
-- email channel, company AND establishment diffusible. Company level (all scopes): the HubSpot view picks per establishment.
-- A company with no eligible email has no row here (that is "no priority email", not "no email").
CREATE OR REPLACE VIEW v_priority_email AS
SELECT DISTINCT ON (k.company_id)
       k.company_id, c.siren, k.contact_id, k.value_normalized AS email, k.value_status,
       k.contact_use, k.contact_use_basis, r.priority_rank, k.establishment_id
FROM company_contacts k
JOIN companies c ON c.company_id = k.company_id
JOIN email_priority_rules r ON r.contact_use = COALESCE(k.contact_use, 'unknown') AND r.eligible
WHERE k.contact_type = 'email' AND k.value_status IN ('verified','sourced') AND k.opposed_at IS NULL
  AND c.is_diffusible
  AND (k.establishment_id IS NULL OR EXISTS (SELECT 1 FROM company_addresses a
                                             WHERE a.establishment_id = k.establishment_id AND a.is_diffusible))
  AND NOT EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'email'
                  AND o.value_normalized = k.value_normalized AND o.channel = 'email')
ORDER BY k.company_id, r.priority_rank, (k.value_status = 'verified') DESC, k.is_primary DESC,
         k.last_verified_at DESC NULLS LAST, k.contact_id;
