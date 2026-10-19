-- Public contracts won by our companies, from the DECP open data (client spec, section 12). Idempotent.
-- A contract is attached to a company ONLY through the titulaire's SIRET/SIREN as published (valid Luhn, company present in our base).
-- Every row keeps the DECP identifiers that prove it (decp_uid / market_id / buyer) and the dataset URL it was read from.
-- "No contract" is NEVER stored: it is derived in v_public_contract_status and only means "none in the datasets we loaded".
CREATE TABLE IF NOT EXISTS decp_loads (
  load_id        BIGSERIAL PRIMARY KEY,
  source_label   TEXT NOT NULL,                 -- e.g. "DECP consolidated 2026-10"
  source_url     TEXT NOT NULL,                 -- dataset / resource URL the file was downloaded from
  file_name      TEXT,
  coverage_note  TEXT,                          -- what the file does and does not cover (period, thresholds, publishers)
  rows_read      INTEGER NOT NULL DEFAULT 0,
  rows_loaded    INTEGER NOT NULL DEFAULT 0,
  rows_duplicate INTEGER NOT NULL DEFAULT 0,
  rows_conflict  INTEGER NOT NULL DEFAULT 0,
  rows_rejected  INTEGER NOT NULL DEFAULT 0,
  rows_not_in_base INTEGER NOT NULL DEFAULT 0,
  notified_from  DATE,
  notified_to    DATE,
  loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_contracts (
  contract_id       BIGSERIAL PRIMARY KEY,
  company_id        BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  siren             CHAR(9) NOT NULL,
  decp_uid          TEXT NOT NULL,              -- DECP uid, else buyer id || market id
  market_id         TEXT,
  buyer_id          TEXT,
  buyer_name        TEXT,
  object            TEXT,
  amount_eur        NUMERIC(16,2),
  amount_suspect    BOOLEAN NOT NULL DEFAULT FALSE,   -- <= 0 or >= 1e9 as published (placeholder / typo): kept, flagged, never summed
  notification_date DATE,
  publication_date  DATE,
  cpv_code          TEXT,
  procedure         TEXT,
  nature            TEXT,
  duration_months   INTEGER,
  execution_place   TEXT,
  titulaire_name    TEXT,                       -- as published
  co_holders        SMALLINT,                   -- titulaires listed on the market (a group = more than 1)
  load_id           BIGINT REFERENCES decp_loads(load_id) ON DELETE SET NULL,
  source_id         SMALLINT REFERENCES data_sources(source_id),
  source_url        TEXT NOT NULL,
  collected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, decp_uid)
);
CREATE INDEX IF NOT EXISTS ix_public_contracts_company ON public_contracts (company_id, notification_date DESC);

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('decp','DECP - Donnees essentielles de la commande publique',3,TRUE,'monthly (consolidated file)','Licence Ouverte; published by buyers, quality varies')
ON CONFLICT (code) DO NOTHING;

-- found / none_in_loaded_datasets / not_checked. 'none_in_loaded_datasets' is NOT "never worked for the public sector":
-- DECP only holds what buyers published (mostly above the legal threshold) and has gaps and duplicates.
CREATE OR REPLACE VIEW v_public_contract_status AS
SELECT c.company_id, c.siren,
       CASE WHEN EXISTS (SELECT 1 FROM public_contracts p WHERE p.company_id = c.company_id) THEN 'found'
            WHEN EXISTS (SELECT 1 FROM decp_loads) THEN 'none_in_loaded_datasets'
            ELSE 'not_checked' END AS public_contract_status,
       (SELECT count(*) FROM public_contracts p WHERE p.company_id = c.company_id) AS contracts,
       (SELECT max(p.notification_date) FROM public_contracts p WHERE p.company_id = c.company_id) AS last_notification
FROM companies c;
