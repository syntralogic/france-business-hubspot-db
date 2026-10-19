-- Certificate link and attestation documents (spec section 11). Idempotent.

-- Link to the certificate / the directory entry, as supplied by the file or the provider. A link, never a scraped page.
ALTER TABLE company_certifications ADD COLUMN IF NOT EXISTS certificate_url TEXT;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_certificate_url_http') THEN
    ALTER TABLE company_certifications ADD CONSTRAINT ck_certificate_url_http
      CHECK (certificate_url IS NULL OR (certificate_url ~* '^https?://[^[:space:]]+$' AND length(certificate_url) <= 2000));
  END IF;
END $$;

-- The document itself (RC pro / decennale attestation, or a copy of a certificate), attached to ONE certification row.
-- The file is stored in the database (max 10 MB, PDF / PNG / JPEG, checked on its first bytes) with its SHA-256, who supplied it
-- and when. It is never exported to HubSpot and never exposed by the API. Same file twice on the same row = one document.
CREATE TABLE IF NOT EXISTS certification_documents (
  document_id      BIGSERIAL PRIMARY KEY,
  certification_id BIGINT NOT NULL REFERENCES company_certifications(certification_id) ON DELETE CASCADE,
  kind             TEXT NOT NULL CHECK (kind IN ('attestation','certificate')),
  file_name        TEXT NOT NULL,
  mime_type        TEXT NOT NULL CHECK (mime_type IN ('application/pdf','image/png','image/jpeg')),
  size_bytes       INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10485760),
  sha256           CHAR(64) NOT NULL,
  content          BYTEA NOT NULL,
  received_from    TEXT NOT NULL,                       -- insurer, client, operator: who supplied the file
  received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  note             TEXT,
  CHECK (octet_length(content) = size_bytes),
  UNIQUE (certification_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_certdoc_cert ON certification_documents(certification_id);

-- One row per certification row, metadata only (never the file).
CREATE OR REPLACE VIEW v_company_certificates AS
SELECT t.certification_id, c.company_id, c.siren, t.cert_type, t.cert_label, t.valid_from, t.valid_to,
       (t.valid_to IS NULL OR t.valid_to >= current_date) AS is_current,
       t.certificate_url, s.code AS source_code, t.load_id,
       (SELECT count(*) FROM certification_documents d WHERE d.certification_id = t.certification_id) AS documents,
       EXISTS (SELECT 1 FROM certification_documents d WHERE d.certification_id = t.certification_id AND d.kind = 'attestation') AS has_attestation,
       (SELECT max(d.received_at) FROM certification_documents d WHERE d.certification_id = t.certification_id) AS last_document_at
FROM company_certifications t
JOIN companies c ON c.company_id = t.company_id
LEFT JOIN data_sources s ON s.source_id = t.source_id;

-- RC pro / decennale: is there an attestation on file, and is it still in date? Only companies that HAVE a row of that type appear:
-- no row means "no insurance row in the files we loaded", which is NOT "uninsured" (see v_certification_status).
--   document_current        an attestation is attached to a row whose end date is today or later
--   document_end_date_unknown  an attestation is attached but the row has no end date: we cannot say it is in date
--   document_expired        attestations exist, all on rows whose end date has passed
--   no_document             the row(s) exist, no attestation attached
CREATE OR REPLACE VIEW v_attestation_status AS
SELECT c.company_id, c.siren, t.cert_type,
       CASE WHEN bool_or(a.has_att AND t.valid_to >= current_date) THEN 'document_current'
            WHEN bool_or(a.has_att AND t.valid_to IS NULL) THEN 'document_end_date_unknown'
            WHEN bool_or(a.has_att) THEN 'document_expired'
            ELSE 'no_document' END AS attestation_status,
       count(*) AS certification_rows
FROM company_certifications t
JOIN companies c ON c.company_id = t.company_id
CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM certification_documents d
                                   WHERE d.certification_id = t.certification_id AND d.kind = 'attestation') AS has_att) a
WHERE t.cert_type IN ('RC pro', 'Decennale')
GROUP BY c.company_id, c.siren, t.cert_type;
