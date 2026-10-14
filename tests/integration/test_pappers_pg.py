"""Pappers loader + daily cap against a REAL PostgreSQL (TEST_DATABASE_URL), API stubbed."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.common import usage  # noqa: E402
from fbdb.enrich import pappers  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class Fake:
    def __init__(self):
        self.calls = []

    def get_company(self, siren):
        self.calls.append(siren)
        if siren == "732829320":
            return None
        if siren == "404833048":
            return {"finances": []}
        return {"finances": [{"annee": 2023, "chiffre_affaires": 500000, "resultat": 1000, "effectif": 4}]}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("""INSERT INTO companies (siren, legal_name, is_diffusible, status, is_sole_proprietor) VALUES
          ('552100554','ALPHA',TRUE,'active',FALSE),('732829320','BETA',TRUE,'active',FALSE),
          ('404833048','GAMMA',TRUE,'active',FALSE),('123456782','SOLO',TRUE,'active',TRUE),('111111118','HIDDEN',FALSE,'active',FALSE)""")


def test_reserve_is_capped_and_release_gives_back(db):
    with psycopg.connect(DSN) as c:
        assert [usage.reserve(c, "x", 3, 2), usage.reserve(c, "x", 3, 2)] == [True, False]
        assert usage.used_today(c, "x") == (1, 2)
        usage.release(c, "x", 2)
        assert usage.used_today(c, "x") == (0, 0)
        assert usage.reserve(c, "x", 1, 2) is False        # a single call costlier than the cap never runs


def test_run_scope_cap_and_idempotence(db):
    fake = Fake()
    s = pappers.run(DSN, fake, limit=10, cap=2)
    assert s["read"] == 2 and s["cap_reached"] and s["credits_used"] == 2
    s2 = pappers.run(DSN, fake, limit=10, cap=10)           # same day: 2 already used, 8 left; 3rd company only
    assert s2["read"] == 1 and not s2["cap_reached"]
    assert sorted(fake.calls) == ["404833048", "552100554", "732829320"]   # sole proprietor and non-diffusible never queried
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT revenue_eur, employees FROM company_financials").fetchall() == [(500000, 4)]
        assert usage.used_today(c, "pappers") == (3, 3)
    s3 = pappers.run(DSN, fake)                             # all checked within 180 days: nothing to do
    assert s3["read"] == 0
