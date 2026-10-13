"""ADEME RGE loader against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import ademe_rge  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SIRET = "55210055400013"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES ('552100554','ALPHA',TRUE,'active')")
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office) SELECT company_id, %s, TRUE FROM companies", (SIRET,))


def test_load_is_idempotent_and_rejects_unknown(db, tmp_path):
    f = tmp_path / "rge.csv"
    f.write_text(f"siret;nom_qualification;date_fin\n{SIRET};QualiPAC;2999-01-01\n{SIRET};Qualibat;\n99999999999999;ZZ;\n", encoding="utf-8")
    rej = tmp_path / "rej.csv"
    s1 = ademe_rge.run(DSN, str(f), str(rej))
    s2 = ademe_rge.run(DSN, str(f), str(rej))
    assert s1 == s2 == {"rows": 3, "matched_rows": 2, "establishments": 1, "rejected": 1}
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT count(*) FROM company_certifications WHERE cert_type='RGE'").fetchone()[0] == 2
    assert "SIRET_NOT_IN_BASE" in rej.read_text(encoding="utf-8")
