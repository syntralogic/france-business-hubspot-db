-- Staging tables for SIRENE loads (truncated at the start of every load).
-- UNLOGGED: faster bulk writes, content is disposable and rebuilt from source files.

CREATE UNLOGGED TABLE IF NOT EXISTS stg_sirene_ul (
  siren                 CHAR(9) PRIMARY KEY,
  legal_name            TEXT,
  trading_name          TEXT,
  legal_form_code       TEXT,
  main_naf              TEXT,
  is_sole_proprietor    BOOLEAN,
  employee_range        TEXT,
  size_category         TEXT,
  status                TEXT,
  creation_date         DATE,
  is_diffusible         BOOLEAN,
  source_last_processed TEXT
);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_sirene_etab (
  siret                 CHAR(14) PRIMARY KEY,
  siren                 CHAR(9) NOT NULL,
  nic                   CHAR(5),
  is_head_office        BOOLEAN,
  establishment_name    TEXT,
  naf                   TEXT,
  status                TEXT,
  creation_date         DATE,
  street                TEXT,
  complement            TEXT,
  postal_code           CHAR(5),
  city                  TEXT,
  department_code       TEXT,
  is_artisan            BOOLEAN,
  is_diffusible         BOOLEAN,
  source_last_processed TEXT
);
CREATE INDEX IF NOT EXISTS ix_stg_etab_siren ON stg_sirene_etab(siren);
