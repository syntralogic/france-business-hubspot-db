-- Spec section 12: documented legal relations between companies, and agency-opening / new-activity events. Idempotent.

-- A legal entity that holds an office (president, gerant, administrateur...) in the company, as written in the register
-- (Annuaire des Entreprises `dirigeants`, type personne morale). It is a DOCUMENTED relation: nothing here comes from a website,
-- and the direction is only "related_siren holds an office in company_id" (typically a holding or a parent). Auditors
-- (commissaires aux comptes) are not kept: they are not a group link.
CREATE TABLE IF NOT EXISTS company_relations (
  relation_id        BIGSERIAL PRIMARY KEY,
  company_id         BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  related_siren      CHAR(9) NOT NULL CHECK (related_siren ~ '^[0-9]{9}$'),
  related_name       TEXT,
  related_company_id BIGINT REFERENCES companies(company_id) ON DELETE SET NULL,   -- set when that SIREN is in our base
  relation_type      TEXT NOT NULL DEFAULT 'legal_entity_officer' CHECK (relation_type IN ('legal_entity_officer')),
  role               TEXT NOT NULL DEFAULT '',
  is_active          BOOLEAN NOT NULL DEFAULT TRUE,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_on           DATE,
  source_id          SMALLINT REFERENCES data_sources(source_id),
  UNIQUE (company_id, related_siren, relation_type, role)
);
CREATE INDEX IF NOT EXISTS ix_relations_related ON company_relations(related_siren) WHERE is_active;

-- Events read from the register (never from a site): a new establishment of an existing company, an activity (NAF) change,
-- and BODACC notices whose own label says so. Derived by fbdb.enrich.register_events; a company is never changed by it.
CREATE TABLE IF NOT EXISTS company_register_events (
  event_id         BIGSERIAL PRIMARY KEY,
  company_id       BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  establishment_id BIGINT REFERENCES company_addresses(establishment_id) ON DELETE CASCADE,
  event_type       TEXT NOT NULL CHECK (event_type IN ('new_establishment','activity_change','bodacc_establishment','bodacc_activity')),
  event_date       DATE NOT NULL,
  detail           TEXT,
  basis            TEXT NOT NULL CHECK (basis IN ('sirene_creation_date','sirene_change_history','bodacc_label')),
  ref_key          TEXT NOT NULL,                  -- siret, change_id or bodacc_id: makes the insert idempotent
  detected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, event_type, ref_key)
);
CREATE INDEX IF NOT EXISTS ix_register_events_date ON company_register_events(event_date DESC);

CREATE OR REPLACE VIEW v_company_relations AS
SELECT r.company_id, c.siren, c.legal_name, r.related_siren, COALESCE(rc.legal_name, r.related_name) AS related_name,
       r.related_company_id IS NOT NULL AS related_in_base, r.role, r.is_active, r.last_verified_at
FROM company_relations r
JOIN companies c ON c.company_id = r.company_id
LEFT JOIN companies rc ON rc.company_id = r.related_company_id;
