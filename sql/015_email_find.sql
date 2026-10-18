-- Memory of Hunter Email Finder attempts per person: a person with no result is not queried (and paid for) again until the refresh delay.
CREATE TABLE IF NOT EXISTS email_find_attempts (
  interlocutor_id BIGINT PRIMARY KEY REFERENCES company_interlocutors(interlocutor_id) ON DELETE CASCADE,
  searched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  domain          TEXT,
  outcome         TEXT NOT NULL CHECK (outcome IN ('found','not_found','invalid','opposed','already_known')),
  score           SMALLINT
);
