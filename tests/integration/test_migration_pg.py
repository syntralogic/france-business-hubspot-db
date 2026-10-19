"""Upgrade path: a database created before closure dates were tracked is brought up to date by sql/006."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _columns(conn):
    rows = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' "
        "AND column_name IN ('closure_date_basis', 'closure_date') AND table_name IN "
        "('companies', 'company_addresses', 'stg_sirene_etab')").fetchall()
    return set(rows)


def test_006_upgrades_an_old_database_and_is_idempotent():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in ("000_reference_data.sql", "001_schema.sql", "002_staging.sql", "003_pipeline_state.sql", "003a_provenance.sql", "003b_financials_exercice.sql", "003c_address_diffusion.sql",
                  "004_export_view.sql"):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        # simulate the pre-006 layout (the view does not read these columns, so they can be dropped)
        conn.execute("ALTER TABLE companies DROP COLUMN closure_date_basis")
        conn.execute("ALTER TABLE company_addresses DROP COLUMN closure_date_basis")
        conn.execute("ALTER TABLE stg_sirene_etab DROP COLUMN closure_date")
        assert ("companies", "closure_date_basis") not in _columns(conn)
        for _ in range(2):                                   # run twice: must not fail
            conn.execute((SQL_DIR / "006_closure_date.sql").read_text(encoding="utf-8"))
        cols = _columns(conn)
        assert ("companies", "closure_date_basis") in cols
        assert ("company_addresses", "closure_date_basis") in cols
        assert ("stg_sirene_etab", "closure_date") in cols
