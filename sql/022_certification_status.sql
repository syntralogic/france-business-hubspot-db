-- Certifications: which file each row came from, when it was last seen, and what "no certification" means (spec section 11).
-- Idempotent.

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('certification_file','Certification / insurance file loaded under a licence (RC pro, decennale, other)',3,FALSE,'per file','Provider and contract are written on each load (certification_loads)')
ON CONFLICT (code) DO NOTHING;

-- One row per file loaded. cert_types = the types that file actually carried (rejected rows do not count).
CREATE TABLE IF NOT EXISTS certification_loads (
  load_id       BIGSERIAL PRIMARY KEY,
  provider      TEXT NOT NULL,
  cert_types    TEXT[] NOT NULL DEFAULT '{}',
  file_name     TEXT,
  contract_ref  TEXT,
  coverage_note TEXT,
  rows_read     INTEGER,
  rows_added    INTEGER,
  rows_unchanged INTEGER,
  rows_rejected INTEGER,
  loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE company_certifications ADD COLUMN IF NOT EXISTS load_id BIGINT REFERENCES certification_loads(load_id);
ALTER TABLE company_certifications ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;   -- last file that carried this row
UPDATE company_certifications SET last_seen_at = collected_at WHERE last_seen_at IS NULL;
ALTER TABLE company_certifications ALTER COLUMN last_seen_at SET DEFAULT now();

-- Rows loaded before this table existed: one 'backfill' load per type, so the status view does not call them "not checked".
INSERT INTO certification_loads (provider, cert_types, coverage_note, loaded_at)
SELECT 'backfill', ARRAY[t.cert_type], 'rows loaded before certification_loads existed: file, contract and date of the load unknown',
       min(t.collected_at)
FROM company_certifications t
WHERE NOT EXISTS (SELECT 1 FROM certification_loads l WHERE t.cert_type = ANY (l.cert_types))
GROUP BY t.cert_type;

-- Per company and per certification type that at least one loaded file carried:
--   found_valid / found_not_current (rows exist, all expired) / none_in_loaded_files.
-- 'none_in_loaded_files' is NOT "not certified": it only says the company is not in the file(s) we loaded (partial files,
-- other establishments, a body that did not publish it). A type that no file ever carried has no row here = not checked.
CREATE OR REPLACE VIEW v_certification_status AS
SELECT c.company_id, c.siren, t.cert_type,
       CASE WHEN EXISTS (SELECT 1 FROM company_certifications x WHERE x.company_id = c.company_id AND x.cert_type = t.cert_type
                           AND (x.valid_to IS NULL OR x.valid_to >= current_date)) THEN 'found_valid'
            WHEN EXISTS (SELECT 1 FROM company_certifications x WHERE x.company_id = c.company_id AND x.cert_type = t.cert_type)
                 THEN 'found_not_current'
            ELSE 'none_in_loaded_files' END AS certification_status,
       (SELECT max(x.last_seen_at) FROM company_certifications x WHERE x.company_id = c.company_id AND x.cert_type = t.cert_type) AS last_seen_at
FROM companies c
CROSS JOIN (SELECT DISTINCT unnest(cert_types) AS cert_type FROM certification_loads) t;
