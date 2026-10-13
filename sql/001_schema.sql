-- France Business DB - core schema (PostgreSQL 16)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE data_sources (
  source_id      SMALLSERIAL PRIMARY KEY,
  code           TEXT NOT NULL UNIQUE,            -- sirene, inpi_rne, pappers, google_places
  name           TEXT NOT NULL,
  priority       SMALLINT NOT NULL,               -- lower = more trusted
  licence_notes  TEXT,
  update_freq    TEXT,
  is_official    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE import_export_batches (
  batch_id       BIGSERIAL PRIMARY KEY,
  batch_type     TEXT NOT NULL CHECK (batch_type IN ('import','export','enrich')),
  source_id      SMALLINT REFERENCES data_sources(source_id),
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at    TIMESTAMPTZ,
  status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','partial','failed')),
  rows_read      INTEGER DEFAULT 0,
  rows_added     INTEGER DEFAULT 0,
  rows_updated   INTEGER DEFAULT 0,
  rows_closed    INTEGER DEFAULT 0,
  rows_unchanged INTEGER DEFAULT 0,
  rows_review    INTEGER DEFAULT 0,
  notes          TEXT
);

-- Legal unit (SIREN)
CREATE TABLE companies (
  company_id         BIGSERIAL PRIMARY KEY,         -- internal record id
  siren              CHAR(9) UNIQUE CHECK (siren ~ '^[0-9]{9}$'),
  legal_name         TEXT NOT NULL,
  trading_name       TEXT,
  legal_form_code    TEXT,
  legal_form_label   TEXT,
  main_naf           TEXT CHECK (main_naf ~ '^[0-9]{2}\.[0-9]{2}[A-Z]$'),
  industry_label     TEXT,
  category           TEXT,                          -- artisan, commercant, liberal, other
  is_sole_proprietor BOOLEAN NOT NULL DEFAULT FALSE,
  is_artisan         BOOLEAN,
  employee_range     TEXT,
  size_category      TEXT,                          -- TPE, PME, ETI, GE
  status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed','dissolved','other')),
  creation_date      DATE,
  closure_date       DATE,
  is_diffusible      BOOLEAN NOT NULL DEFAULT TRUE,
  primary_source_id  SMALLINT REFERENCES data_sources(source_id),
  confidence_score   NUMERIC(4,3) CHECK (confidence_score BETWEEN 0 AND 1),
  dup_status         TEXT NOT NULL DEFAULT 'unique' CHECK (dup_status IN ('unique','merged','review')),
  enrichment_status  TEXT NOT NULL DEFAULT 'none',
  freshness_status   TEXT NOT NULL DEFAULT 'fresh',
  missing_fields     TEXT[],
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at   TIMESTAMPTZ
);
CREATE INDEX ix_companies_status ON companies(status);
CREATE INDEX ix_companies_naf ON companies(main_naf);
CREATE INDEX ix_companies_name_trgm ON companies USING gin (legal_name gin_trgm_ops);

-- Establishment (SIRET). Each company has 1..n establishments.
CREATE TABLE company_addresses (
  establishment_id     BIGSERIAL PRIMARY KEY,
  company_id           BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  siret                CHAR(14) UNIQUE CHECK (siret ~ '^[0-9]{14}$'),
  nic                  CHAR(5),
  is_head_office       BOOLEAN NOT NULL DEFAULT FALSE,
  establishment_name   TEXT,
  naf                  TEXT,
  status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed','other')),
  creation_date        DATE,
  closure_date         DATE,
  street               TEXT,
  complement           TEXT,
  postal_code          CHAR(5) CHECK (postal_code IS NULL OR postal_code ~ '^[0-9]{2}[0-9AB][0-9]{2}$'),
  city                 TEXT,
  department_code      TEXT,
  department_name      TEXT,
  region               TEXT,
  country              TEXT NOT NULL DEFAULT 'France',
  latitude             NUMERIC(9,6),
  longitude            NUMERIC(9,6),
  standardized_address TEXT,
  address_status       TEXT NOT NULL DEFAULT 'unverified' CHECK (address_status IN ('verified','sourced','unverified','invalid')),
  address_hash         CHAR(40),
  source_id            SMALLINT REFERENCES data_sources(source_id),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_addr_company ON company_addresses(company_id);
CREATE INDEX ix_addr_postal ON company_addresses(postal_code);
CREATE INDEX ix_addr_dept ON company_addresses(department_code);
CREATE INDEX ix_addr_hash ON company_addresses(address_hash);
-- A company has at most one active head office
CREATE UNIQUE INDEX ux_one_head_office ON company_addresses(company_id) WHERE is_head_office AND status = 'active';

CREATE TABLE company_contacts (
  contact_id       BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  establishment_id BIGINT REFERENCES company_addresses(establishment_id) ON DELETE CASCADE,
  contact_type     TEXT NOT NULL CHECK (contact_type IN ('phone','email','website','linkedin','social')),
  value            TEXT NOT NULL,
  value_normalized TEXT NOT NULL,                   -- E.164, lowercase email, bare domain
  is_primary       BOOLEAN NOT NULL DEFAULT FALSE,
  value_status     TEXT NOT NULL CHECK (value_status IN ('verified','sourced','inferred','unavailable')),
  source_id        SMALLINT REFERENCES data_sources(source_id),
  collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ,
  UNIQUE (company_id, contact_type, value_normalized)
);
CREATE INDEX ix_contacts_norm ON company_contacts(contact_type, value_normalized);

CREATE TABLE company_directors (
  director_id      BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  first_name       TEXT,
  last_name        TEXT,
  role             TEXT,                            -- Gerant, President, ...
  birth_year       SMALLINT,                        -- year only (data minimization)
  is_active        BOOLEAN NOT NULL DEFAULT TRUE,
  start_date       DATE,
  end_date         DATE,
  source_id        SMALLINT REFERENCES data_sources(source_id),
  last_verified_at TIMESTAMPTZ,
  UNIQUE (company_id, last_name, first_name, role)
);
CREATE INDEX ix_directors_company ON company_directors(company_id);

CREATE TABLE company_financials (
  financial_id   BIGSERIAL PRIMARY KEY,
  company_id     BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  fiscal_year    SMALLINT NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
  revenue_eur    NUMERIC(16,2),
  net_result_eur NUMERIC(16,2),
  employees      INTEGER,
  source_id      SMALLINT REFERENCES data_sources(source_id),
  collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, fiscal_year, source_id)
);

CREATE TABLE company_certifications (
  certification_id BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  establishment_id BIGINT REFERENCES company_addresses(establishment_id),
  cert_type        TEXT NOT NULL,                   -- RGE, Qualibat, ...
  cert_label       TEXT,
  valid_from       DATE,
  valid_to         DATE,
  source_id        SMALLINT REFERENCES data_sources(source_id),
  collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, cert_type, cert_label, valid_from)
);

CREATE TABLE company_reviews (
  review_id         BIGSERIAL PRIMARY KEY,
  establishment_id  BIGINT NOT NULL REFERENCES company_addresses(establishment_id) ON DELETE CASCADE,
  provider          TEXT NOT NULL DEFAULT 'google_places',
  provider_place_id TEXT,
  rating            NUMERIC(2,1) CHECK (rating BETWEEN 0 AND 5),
  review_count      INTEGER,
  profile_url       TEXT,
  match_confidence  NUMERIC(4,3),
  collected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (establishment_id, provider)
);

-- Raw payload per source record, kept for audit and re-processing
CREATE TABLE company_source_records (
  record_id        BIGSERIAL PRIMARY KEY,
  source_id        SMALLINT NOT NULL REFERENCES data_sources(source_id),
  external_key     TEXT NOT NULL,                   -- siret, siren, place_id...
  company_id       BIGINT REFERENCES companies(company_id),
  establishment_id BIGINT REFERENCES company_addresses(establishment_id),
  payload          JSONB NOT NULL,
  payload_hash     CHAR(64) NOT NULL,
  batch_id         BIGINT REFERENCES import_export_batches(batch_id),
  fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  match_status     TEXT NOT NULL DEFAULT 'matched' CHECK (match_status IN ('matched','new','review','rejected')),
  UNIQUE (source_id, external_key, payload_hash)
);
CREATE INDEX ix_srcrec_company ON company_source_records(company_id);

CREATE TABLE company_change_history (
  change_id        BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  establishment_id BIGINT REFERENCES company_addresses(establishment_id),
  table_name       TEXT NOT NULL,
  field_name       TEXT NOT NULL,
  old_value        TEXT,
  new_value        TEXT,
  change_type      TEXT NOT NULL CHECK (change_type IN ('insert','update','close','reopen','delete')),
  source_id        SMALLINT REFERENCES data_sources(source_id),
  batch_id         BIGINT REFERENCES import_export_batches(batch_id),
  changed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_change_company ON company_change_history(company_id, changed_at DESC);
CREATE INDEX ix_change_batch ON company_change_history(batch_id);

CREATE TABLE data_quality_issues (
  issue_id         BIGSERIAL PRIMARY KEY,
  company_id       BIGINT REFERENCES companies(company_id) ON DELETE CASCADE,
  establishment_id BIGINT REFERENCES company_addresses(establishment_id),
  batch_id         BIGINT REFERENCES import_export_batches(batch_id),
  issue_code       TEXT NOT NULL,                   -- INVALID_SIREN, BAD_PHONE, DUP_CANDIDATE...
  severity         TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
  field_name       TEXT,
  details          TEXT,
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','ignored')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at      TIMESTAMPTZ
);
CREATE INDEX ix_dqi_open ON data_quality_issues(status, issue_code);

INSERT INTO data_sources(code,name,priority,is_official,update_freq,licence_notes) VALUES
 ('sirene','INSEE SIRENE',1,TRUE,'daily/monthly bulk','Licence Ouverte; respect non-diffusion'),
 ('inpi_rne','INPI RNE',1,TRUE,'daily','Open data; GDPR for directors'),
 ('annuaire','Annuaire des Entreprises API',2,TRUE,'periodic','Open data'),
 ('bodacc','BODACC',2,TRUE,'daily','Open licence'),
 ('ademe_rge','ADEME RGE',2,TRUE,'monthly','Open data'),
 ('pappers','Pappers API',3,FALSE,'near real time','Paid; verify storage terms'),
 ('licensed_contacts','Licensed contact provider',4,FALSE,'per contract','Contract required'),
 ('google_places','Google Places API',5,FALSE,'on demand','ToS limits storage');
