"""INPI cross-check against a REAL PostgreSQL (TEST_DATABASE_URL), API stubbed."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import inpi  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class FakeClient:
    def __init__(self, notices):
        self.notices = notices

    def get_company(self, siren):
        v = self.notices.get(siren)
        if isinstance(v, Exception):
            raise v
        return v


def notice(name, radiation=None):
    n = {"pouvoirs": [{"individu": {"descriptionPersonne": {"nom": name}}}]}
    if radiation:
        n["dateRadiation"] = radiation
    return {"formality": n}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("""INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES
                        ('552100554','ALPHA',TRUE,'active'),('732829320','BETA',TRUE,'active'),('404833048','GAMMA',TRUE,'active')""")
        conn.execute("""INSERT INTO company_directors (company_id, last_name, first_name, role, is_active)
                        SELECT company_id, 'DUPONT', 'Anne', 'Gerant', TRUE FROM companies WHERE siren='552100554'""")


def test_flags_idempotent_and_error_isolation(db):
    cl = FakeClient({"552100554": notice("Martin", "2026-09-30"), "732829320": None,
                     "404833048": RuntimeError("boom")})
    s1 = inpi.run(DSN, cl)
    assert s1 == {"read": 3, "radiation_flags": 1, "director_flags": 1, "not_found": 1, "errors": 1}
    s2 = inpi.run(DSN, cl)  # BETA checked, ALPHA checked: only GAMMA (error, never stamped) is retried
    assert s2["read"] == 1 and s2["errors"] == 1
    with psycopg.connect(DSN) as c:
        codes = sorted(r[0] for r in c.execute("SELECT issue_code FROM data_quality_issues"))
        assert codes == ["INPI_DIRECTOR_MISMATCH", "INPI_RADIATION_PRESENT"]
        assert c.execute("SELECT status FROM companies WHERE siren='552100554'").fetchone()[0] == "active"
        assert c.execute("SELECT status FROM import_export_batches ORDER BY batch_id DESC LIMIT 1").fetchone()[0] == "partial"
