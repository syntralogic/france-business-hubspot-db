-- Operations: one row per scheduled job run, so a missed or failed weekly run is detectable from the database. Idempotent.
CREATE TABLE IF NOT EXISTS job_runs (
  run_id      BIGSERIAL PRIMARY KEY,
  job         TEXT NOT NULL,
  started_at  TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status      TEXT NOT NULL CHECK (status IN ('ok','partial','failed')),
  failed_steps TEXT,
  log_path    TEXT
);
CREATE INDEX IF NOT EXISTS ix_job_runs_job ON job_runs(job, finished_at DESC);
