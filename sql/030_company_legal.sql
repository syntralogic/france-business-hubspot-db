-- Legal sheet fields (Pappers): capital, VAT, RCS, corporate purpose, collective agreement, closing date; and document METADATA.
-- Document download tokens are never stored (they are tied to the API key and expire).
CREATE TABLE IF NOT EXISTS company_legal_info (
  company_id            BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  share_capital_eur     NUMERIC(16,2),
  vat_number            TEXT,
  rcs_number            TEXT,
  rcs_registry          TEXT,
  rcs_status            TEXT,
  rcs_registered_on     DATE,
  corporate_purpose     TEXT,
  exercise_forms        TEXT,
  collective_agreements TEXT,
  fiscal_year_end       TEXT,
  next_closing_date     DATE,
  commercial_opposition BOOLEAN,          -- provider flag: the company objects to commercial use of its data; respect it on export
  source_id             SMALLINT REFERENCES data_sources(source_id),
  collected_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_documents (
  document_id  BIGSERIAL PRIMARY KEY,
  company_id   BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('statutes', 'accounts', 'act', 'registration_extract')),
  doc_date     DATE,
  label        TEXT,
  available    BOOLEAN,
  source_id    SMALLINT REFERENCES data_sources(source_id),
  collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_documents ON company_documents (company_id, kind, COALESCE(doc_date, DATE '1900-01-01'), COALESCE(label, ''));
