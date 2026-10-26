-- Direct mobile of a named person from a licensed provider (Lusha-type person enrichment). Idempotent.
-- Memory per person: nobody is queried (and paid for) again before the refresh delay.
CREATE TABLE IF NOT EXISTS mobile_find_attempts (
  interlocutor_id BIGINT PRIMARY KEY REFERENCES company_interlocutors(interlocutor_id) ON DELETE CASCADE,
  searched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  domain          TEXT,
  outcome         TEXT NOT NULL CHECK (outcome IN ('found','not_found','no_mobile','do_not_call','opposed','invalid','other_company','already_known'))
);
INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('lusha','Lusha person enrichment (direct mobile)',5,FALSE,'on demand','Paid, needs a contract covering B2B prospecting and storage; every number is provider-attributed, never verified by us')
ON CONFLICT (code) DO NOTHING;
