-- BODACC announcements (Chunk 2). Idempotent. Events are stored per announcement id; nothing here changes company status
-- (SIRENE stays the authority). Events for SIRENs in our base also raise a data_quality_issues row (see enrich/bodacc.py).
CREATE TABLE IF NOT EXISTS bodacc_events (
  bodacc_id     TEXT PRIMARY KEY,                -- announcement id, e.g. B202600123456
  siren         CHAR(9) NOT NULL,
  family        TEXT NOT NULL,                   -- familleavis (collective, radiation, ...)
  family_label  TEXT,
  avis_type     TEXT,
  published_on  DATE NOT NULL,
  court         TEXT,
  department    TEXT,
  url           TEXT,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bodacc_siren ON bodacc_events(siren, published_on DESC);
