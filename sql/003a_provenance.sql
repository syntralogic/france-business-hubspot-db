-- Provenance, opposition and enrichment-check columns (spec gaps). Idempotent.
-- Named 003a so it runs BEFORE 004_export_view.sql (the view reads opposed_at).
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS platform     TEXT;          -- facebook, instagram, tiktok, youtube, x ... for contact_type 'social'
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS match_proof  TEXT;          -- preuve du rapprochement, e.g. "SIREN 552100554 found on https://x.fr/mentions-legales"
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS match_url    TEXT;
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS line_type    TEXT;          -- landline / mobile / voip (Twilio Lookup)
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS verify_result TEXT;         -- provider verdict for emails (valid, invalid, accept_all, ...)
ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS checked_at   TIMESTAMPTZ;   -- last provider check (email verifier / line type)
-- LEGACY per-row flag (all channels). Still honoured by the export view (fail closed) but no longer written: use contact_oppositions.
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

-- Opposition a la prospection, PER CHANNEL and keyed by the VALUE (not by a contact row), so the same email/phone stays blocked
-- when it comes back through another company, a re-import or a later enrichment, and can be recorded before we even hold it.
-- Channels: email -> 'email'; phone -> 'call' and 'sms' (independent). Exports and verifiers must check this table.
CREATE TABLE IF NOT EXISTS contact_oppositions (
  opposition_id    BIGSERIAL PRIMARY KEY,
  contact_type     TEXT NOT NULL CHECK (contact_type IN ('email','phone')),
  value_normalized TEXT NOT NULL,
  channel          TEXT NOT NULL CHECK (channel IN ('email','call','sms')),
  opposed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  note             TEXT,
  UNIQUE (contact_type, value_normalized, channel),
  CHECK ((contact_type = 'email' AND channel = 'email') OR (contact_type = 'phone' AND channel IN ('call','sms')))
);
-- one-off carry-over of the legacy per-row flag: it meant "every channel"
INSERT INTO contact_oppositions (contact_type, value_normalized, channel, opposed_at, note)
  SELECT 'email', value_normalized, 'email', min(opposed_at), min(opposition_note) FROM company_contacts
  WHERE contact_type = 'email' AND opposed_at IS NOT NULL GROUP BY value_normalized
ON CONFLICT DO NOTHING;
INSERT INTO contact_oppositions (contact_type, value_normalized, channel, opposed_at, note)
  SELECT 'phone', k.value_normalized, ch.channel, min(k.opposed_at), min(k.opposition_note)
  FROM company_contacts k CROSS JOIN (VALUES ('call'), ('sms')) AS ch(channel)
  WHERE k.contact_type = 'phone' AND k.opposed_at IS NOT NULL GROUP BY k.value_normalized, ch.channel
ON CONFLICT DO NOTHING;
