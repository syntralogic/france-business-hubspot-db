-- NAF reference: lets us fill "trade" (metier) and category without guessing. Populate from INSEE NAF rev.2.
CREATE TABLE IF NOT EXISTS naf_reference (
  naf      TEXT PRIMARY KEY CHECK (naf ~ '^[0-9]{2}\.[0-9]{2}[A-Z]$'),
  label    TEXT NOT NULL,
  trade    TEXT,          -- e.g. Boulanger, Plombier (curated mapping, optional)
  category TEXT           -- artisan / commercant / liberal / other
);

-- One row per exportable establishment, shaped for src/fbdb/export/hubspot.py (build_row keys).
CREATE OR REPLACE VIEW v_hubspot_companies AS
SELECT
  a.establishment_id            AS internal_id,
  a.siret, c.siren, c.legal_name, c.trading_name, a.establishment_name,
  a.street, a.complement, a.postal_code, a.city, a.department_code,
  COALESCE(a.department_name, dr.name)        AS department_name,   -- department_reference (sql/000)
  COALESCE(a.region, dr.region_name)          AS region,
  a.country,
  COALESCE(a.naf, c.main_naf)   AS naf,
  COALESCE(nr.label, c.industry_label) AS naf_label,
  nr.trade                      AS trade,
  -- known code -> label; unknown code -> the raw code (never a guessed label)
  COALESCE(c.legal_form_label, lf.label, c.legal_form_code) AS legal_form,
  a.is_head_office, a.status, a.creation_date, a.closure_date,
  c.size_category,
  COALESCE(er.label, c.employee_range)        AS employee_range,
  c.is_diffusible AS is_diffusible,
  ph.value_normalized AS phone,   ph.value_status AS phone_status,
  em.value_normalized AS email,   em.value_status AS email_status,
  COALESCE(ph.source_code, em.source_code) AS contact_source,
  ws.value AS website, li.value AS linkedin,
  fin.revenue_eur AS revenue, fin.fiscal_year AS revenue_year, fin.employees AS employees,
  certs.certifications,
  rge.rge_labels, rge.rge_until,
  dir.first_name AS director_first, dir.last_name AS director_last, dir.role AS director_role,
  src.code AS primary_source,
  c.last_verified_at, c.confidence_score AS confidence, c.freshness_status AS freshness,
  rv.rating AS google_rating, rv.review_count AS google_reviews, rv.profile_url AS google_url,
  -- not exported to the CSV: SMS opposition is separate from calls, so downstream SMS tools must check this flag
  (ph.value_normalized IS NOT NULL AND NOT EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'phone'
     AND o.value_normalized = ph.value_normalized AND o.channel = 'sms')) AS phone_sms_allowed
FROM company_addresses a
JOIN companies c ON c.company_id = a.company_id
LEFT JOIN data_sources src ON src.source_id = c.primary_source_id
LEFT JOIN naf_reference nr ON nr.naf = COALESCE(a.naf, c.main_naf)
LEFT JOIN department_reference dr ON dr.code = a.department_code
LEFT JOIN legal_form_reference lf ON lf.code = c.legal_form_code
LEFT JOIN employee_range_reference er ON er.code = c.employee_range
LEFT JOIN LATERAL (
  SELECT k.value_normalized, k.value_status, s.code AS source_code FROM company_contacts k
  LEFT JOIN data_sources s ON s.source_id = k.source_id
  WHERE k.company_id = c.company_id AND (k.establishment_id = a.establishment_id OR k.establishment_id IS NULL)
    AND k.contact_type = 'phone' AND k.value_status IN ('verified','sourced') AND k.opposed_at IS NULL
    AND NOT EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'phone' AND o.value_normalized = k.value_normalized AND o.channel = 'call')   -- the exported phone is for calls
  ORDER BY (k.establishment_id IS NOT NULL) DESC, k.is_primary DESC, k.last_verified_at DESC NULLS LAST LIMIT 1) ph ON TRUE
LEFT JOIN LATERAL (
  SELECT k.value_normalized, k.value_status, s.code AS source_code FROM company_contacts k
  LEFT JOIN data_sources s ON s.source_id = k.source_id
  WHERE k.company_id = c.company_id AND (k.establishment_id = a.establishment_id OR k.establishment_id IS NULL)
    AND k.contact_type = 'email' AND k.value_status IN ('verified','sourced') AND k.opposed_at IS NULL   -- never 'inferred'
    AND NOT EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'email' AND o.value_normalized = k.value_normalized AND o.channel = 'email')
  ORDER BY (k.establishment_id IS NOT NULL) DESC, k.is_primary DESC, k.last_verified_at DESC NULLS LAST LIMIT 1) em ON TRUE
LEFT JOIN LATERAL (
  SELECT k.value FROM company_contacts k
  WHERE k.company_id = c.company_id AND (k.establishment_id = a.establishment_id OR k.establishment_id IS NULL)
    AND k.contact_type = 'website' AND k.value_status IN ('verified','sourced')
  ORDER BY (k.establishment_id IS NOT NULL) DESC, k.is_primary DESC LIMIT 1) ws ON TRUE
LEFT JOIN LATERAL (
  SELECT k.value FROM company_contacts k
  WHERE k.company_id = c.company_id AND k.contact_type = 'linkedin' AND k.value_status IN ('verified','sourced')
  ORDER BY k.is_primary DESC LIMIT 1) li ON TRUE
LEFT JOIN LATERAL (
  SELECT f.revenue_eur, f.fiscal_year, f.employees FROM company_financials f
  WHERE f.company_id = c.company_id AND f.revenue_eur IS NOT NULL ORDER BY f.fiscal_year DESC LIMIT 1) fin ON TRUE
LEFT JOIN LATERAL (
  SELECT string_agg(DISTINCT x.cert_type, '; ' ORDER BY x.cert_type) AS certifications FROM company_certifications x
  WHERE x.company_id = c.company_id AND (x.valid_to IS NULL OR x.valid_to >= current_date)) certs ON TRUE
LEFT JOIN LATERAL (   -- ADEME RGE: this establishment only, certificates still valid today
  SELECT string_agg(DISTINCT x.cert_label, '; ' ORDER BY x.cert_label) AS rge_labels, max(x.valid_to) AS rge_until
  FROM company_certifications x
  WHERE x.establishment_id = a.establishment_id AND x.cert_type = 'RGE'
    AND (x.valid_to IS NULL OR x.valid_to >= current_date)) rge ON TRUE
LEFT JOIN LATERAL (
  SELECT d.first_name, d.last_name, d.role FROM company_directors d
  WHERE d.company_id = c.company_id AND d.is_active ORDER BY d.start_date DESC NULLS LAST LIMIT 1) dir ON TRUE
LEFT JOIN LATERAL (
  SELECT r.rating, r.review_count, r.profile_url FROM company_reviews r
  WHERE r.establishment_id = a.establishment_id AND r.match_confidence >= 0.8 LIMIT 1) rv ON TRUE
WHERE c.is_diffusible;
