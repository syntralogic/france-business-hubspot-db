"""Metrics against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("""INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES
                        ('552100554','ALPHA',TRUE,'active'),('732829320','BETA',TRUE,'active'),('404833048','HIDDEN',FALSE,'active')""")
        conn.execute("""INSERT INTO company_addresses (company_id, siret, is_head_office, city, postal_code, address_status, latitude, longitude)
                        SELECT company_id, siren || '00013', TRUE, 'Paris', '75002',
                               CASE WHEN siren='552100554' THEN 'verified' ELSE 'unverified' END,
                               CASE WHEN siren='552100554' THEN 48.869 END, CASE WHEN siren='552100554' THEN 2.331 END
                        FROM companies""")
        conn.execute("INSERT INTO company_addresses (company_id, siret, status) SELECT company_id, '55210055400021', 'closed' FROM companies WHERE siren='552100554'")
        conn.execute("""INSERT INTO company_directors (company_id, last_name, first_name, role, is_active)
                        SELECT company_id, 'DUPONT', 'Anne', 'Gerant', TRUE FROM companies WHERE siren='552100554'""")


def test_collect_counts_only_exportable(db):
    m, rates = metrics.collect(DSN, target_rows=10)
    assert m["active"] == 3 and m["exportable"] == 2          # non-diffusible company and closed establishment never exported
    assert round(m["coverage"], 3) == round(2 / 3, 3) and m["volume_vs_target"] == 0.2
    assert rates["city"] == 1.0 and rates["phone"] == 0.0
    assert rates["match_geocode_verified"] == 0.5 and rates["match_geocode_coordinates"] == 0.5
    assert rates["match_directors_non_sole_prop_companies"] == 0.5   # 1 of 2 diffusible companies
    rows = metrics.evaluate(rates, metrics.DEFAULT_TARGETS)
    assert "match_geocode_verified" in {r["metric"] for r in rows if r["status"] == "GAP"}
