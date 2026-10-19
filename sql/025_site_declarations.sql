-- What a company DECLARES on its own proven site about WHERE it works and HOW BIG its team is (client spec sections 2 and 4). Idempotent.
-- A claim of the site, stored as written with its passage, page URL and the SIREN proof of the site. Nothing is resolved to an
-- INSEE code or a distance, and the declared team size is kept APART from the official headcount (company_financials.employees).
-- The company address (SIRENE) and its service area are two different things: a service area is never written into company_addresses.
CREATE TABLE IF NOT EXISTS company_site_declarations (
  declaration_id BIGSERIAL PRIMARY KEY,
  company_id     BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  kind           TEXT NOT NULL CHECK (kind IN ('service_area_place','service_area_radius_km','service_area_scope','team_size')),
  value          TEXT NOT NULL CHECK (value <> ''),   -- place name as written / "30 km autour de X" / national|regional / team size
  number         NUMERIC(12,2),                      -- km for a radius, headcount for team_size
  qualifier      TEXT CHECK (qualifier IS NULL OR qualifier IN ('exact','plus_de','environ','pres_de')),
  matched_text   TEXT,
  passage        TEXT NOT NULL,                      -- the sentence / line of the page that states it
  source_url     TEXT NOT NULL,
  site_proof     TEXT,
  source_id      SMALLINT REFERENCES data_sources(source_id),
  collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- last run that still read it; never deleted because it disappeared
  UNIQUE (company_id, kind, value)
);
CREATE INDEX IF NOT EXISTS ix_site_declarations_kind ON company_site_declarations (kind, value);
