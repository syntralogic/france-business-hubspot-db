-- Watermarks and small key/value state for incremental runs
CREATE TABLE IF NOT EXISTS pipeline_state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
