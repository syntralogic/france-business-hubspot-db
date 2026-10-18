"""Regression: only genuinely NEW establishments get an 'insert' history row.

Found by scripts/synthetic_weekly.py: establishments loaded by the bulk loader have no history, so an old query
('no insert history yet') labelled every later-updated establishment as new (10,200 bogus rows for 1,700 real ones).
"""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.load import weekly  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SIREN, S1, S2, S3 = "552100554", "55210055400013", "55210055400021", "55210055400039"


def row(siret, street, siege="false"):
    return {"siren": SIREN, "siret": siret, "nic": siret[9:], "statutDiffusionEtablissement": "O",
            "etablissementSiege": siege, "etatAdministratifEtablissement": "A", "libelleVoieEtablissement": street,
            "codePostalEtablissement": "69003", "libelleCommuneEtablissement": "LYON 3EME",
            "dateDernierTraitementEtablissement": "2026-10-12T09:00:00.000000", "statutDiffusionUniteLegale": "O",
            "etatAdministratifUniteLegale": "A", "denominationUniteLegale": "TEST SA", "categorieJuridiqueUniteLegale": "5710",
            "dateDernierTraitementUniteLegale": "2026-10-12T09:00:00.000000"}


class Fake:
    def __init__(self, rows):
        self.rows = rows

    def changed_since(self, since):
        return iter(self.rows)


def test_updated_establishment_is_not_logged_as_insert():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in ("000_reference_data.sql", "001_schema.sql", "002_staging.sql", "003_pipeline_state.sql", "003a_provenance.sql",
                  "004_export_view.sql", "006_closure_date.sql"):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        # state as left by the bulk loader: company + two establishments, NO history rows
        cid = conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s, 'TEST SA') RETURNING company_id", (SIREN,)).fetchone()[0]
        for siret, hq in ((S1, True), (S2, False)):
            conn.execute("INSERT INTO company_addresses (company_id, siret, nic, is_head_office, street, postal_code, city) "
                         "VALUES (%s, %s, %s, %s, '1 RUE A', '69003', 'LYON 3EME')", (cid, siret, siret[9:], hq))
    stats = weekly.run("2026-10-01T00:00:00", DSN, client=Fake([row(S1, "NOUVELLE RUE", "true"), row(S3, "1 RUE A")]))
    with psycopg.connect(DSN) as conn:
        inserts = [r[0] for r in conn.execute("SELECT new_value FROM company_change_history WHERE field_name='siret' AND change_type='insert'")]
        street = conn.execute("SELECT count(*) FROM company_change_history WHERE field_name='street' AND change_type='update'").fetchone()[0]
    assert inserts == [S3]                       # S1 was only updated: it must NOT appear here
    assert street == 1
    assert stats["added_establishments"] == 1
