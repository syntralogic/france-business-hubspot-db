-- Interlocuteurs (client spec, section 5): several people per company, each with their own phones/emails.
-- A legal director (company_directors, from the register) and an operational contact (team page, contact page) are both
-- interlocutors; the legal role and the operational function are kept apart. Idempotent.
CREATE TABLE IF NOT EXISTS company_interlocutors (
  interlocutor_id       BIGSERIAL PRIMARY KEY,
  company_id            BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  director_id           BIGINT REFERENCES company_directors(director_id) ON DELETE SET NULL,   -- set when the person is a register director
  first_name            TEXT,
  last_name             TEXT NOT NULL,
  legal_role            TEXT,                         -- Gerant, President ... as published by the register
  operational_function  TEXT,                         -- as written on the team/contact page
  function_category     TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (function_category IN ('director','sales','project_manager','admin','technical','other','unknown')),
  profile_url           TEXT,                         -- public professional profile, only if matched to the company
  match_proof           TEXT,                         -- why this person belongs to this company
  source_id             SMALLINT REFERENCES data_sources(source_id),
  source_url            TEXT,
  source_date           DATE,                         -- date of the source (a stale team page may list someone who left)
  is_active             BOOLEAN NOT NULL DEFAULT TRUE,
  decision_maker_confirmed BOOLEAN NOT NULL DEFAULT FALSE,   -- only after a conversation: a job title alone never proves it
  confirmation_note     TEXT,
  confirmed_at          TIMESTAMPTZ,
  collected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at      TIMESTAMPTZ,
  CHECK (NOT decision_maker_confirmed OR (confirmation_note IS NOT NULL AND confirmed_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_interlocutor_person
  ON company_interlocutors (company_id, lower(last_name), lower(coalesce(first_name, '')), lower(coalesce(operational_function, '')));
CREATE INDEX IF NOT EXISTS ix_interlocutor_company ON company_interlocutors(company_id);

-- A phone/email can belong to a person. The link needs an evidence level (never a bare guess), and records what the
-- contact is used for. Without a link the value stays "company - person not identified".
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS interlocutor_id BIGINT REFERENCES company_interlocutors(interlocutor_id) ON DELETE SET NULL;
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS attribution_level TEXT
  CHECK (attribution_level IN ('published_with_name','provider_attributed','confirmed_by_contact'));
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS contact_use TEXT
  CHECK (contact_use IN ('switchboard','direction','sales','emergency','after_sales','accounting','recruitment','general'));
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_contact_person_needs_evidence') THEN
    ALTER TABLE company_contacts ADD CONSTRAINT ck_contact_person_needs_evidence
      CHECK (interlocutor_id IS NULL OR attribution_level IS NOT NULL);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_contacts_interlocutor ON company_contacts(interlocutor_id) WHERE interlocutor_id IS NOT NULL;

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('team_page','Company team/contact page',4,FALSE,'on demand','Public page; keep source URL and date'),
 ('manual','Manual entry / call outcome',4,FALSE,'on demand','Entered by an operator')
ON CONFLICT (code) DO NOTHING;

-- One row per person x contact, opposition-aware (same rules as the HubSpot view: call/email channels, legacy flag).
CREATE OR REPLACE VIEW v_interlocutor_contacts AS
SELECT i.interlocutor_id, i.company_id, c.siren, i.first_name, i.last_name, i.legal_role, i.operational_function,
       i.function_category, i.decision_maker_confirmed, i.is_active,
       k.contact_id, k.contact_type, k.value_normalized AS value, k.line_type, k.verify_result,
       k.attribution_level, k.contact_use, k.is_primary
FROM company_interlocutors i
JOIN companies c ON c.company_id = i.company_id
LEFT JOIN company_contacts k ON k.interlocutor_id = i.interlocutor_id
  AND k.opposed_at IS NULL AND k.value_status <> 'unavailable'
  AND NOT (k.contact_type = 'phone' AND EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'phone'
           AND o.value_normalized = k.value_normalized AND o.channel = 'call'))
  AND NOT (k.contact_type = 'email' AND EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'email'
           AND o.value_normalized = k.value_normalized AND o.channel = 'email'));
