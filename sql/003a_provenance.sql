-- Provenance, opposition and enrichment-check columns (spec gaps). Idempotent.
-- Named 003a so it runs BEFORE 004_export_view.sql (the view reads opposed_at).
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS platform     TEXT;          -- facebook, instagram, tiktok, youtube, x ... for contact_type 'social'
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS match_proof  TEXT;          -- preuve du rapprochement, e.g. "SIREN 552100554 found on https://x.fr/mentions-legales"
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS match_url    TEXT;
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS line_type    TEXT;          -- landline / mobile / voip (Twilio Lookup)
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS verify_result TEXT;         -- provider verdict for emails (valid, invalid, accept_all, ...)
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS checked_at   TIMESTAMPTZ;   -- last provider check (email verifier / line type)
-- Opposition a la prospection, per contact and per channel. A set date means: do NOT export or use this value for that channel.
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS opposed_at   TIMESTAMPTZ;
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS opposition_note TEXT;
CREATE INDEX IF NOT EXISTS ix_contacts_unchecked ON company_contacts(contact_type, checked_at NULLS FIRST);

-- Conflicting values between sources, kept instead of silently overwritten.
CREATE TABLE IF NOT EXISTS field_conflicts (
  conflict_id    BIGSERIAL PRIMARY KEY,
  company_id     BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  establishment_id BIGINT REFERENCES company_addresses(establishment_id),
  field_name     TEXT NOT NULL,
  kept_value     TEXT,
  kept_source_id SMALLINT REFERENCES data_sources(source_id),
  other_value    TEXT,
  other_source_id SMALLINT REFERENCES data_sources(source_id),
  detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','ignored')),
  UNIQUE (company_id, field_name, other_value, other_source_id)
);

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('website','Company website (mentions legales)',4,FALSE,'on demand','Public pages; only values proven by a SIREN on the site'),
 ('hunter','Hunter email verifier',5,FALSE,'on demand','Paid; verification verdict only'),
 ('dropcontact','Dropcontact',5,FALSE,'on demand','Paid; verify GDPR terms'),
 ('twilio_lookup','Twilio Lookup',5,FALSE,'on demand','Paid; line type only'),
 ('qualibat','Qualibat',3,FALSE,'per contract/file','Licence to verify'),
 ('qualifelec','Qualifelec',3,FALSE,'per contract/file','Licence to verify'),
 ('qualitenr','Qualit''EnR',3,FALSE,'per contract/file','Licence to verify')
ON CONFLICT (code) DO NOTHING;
