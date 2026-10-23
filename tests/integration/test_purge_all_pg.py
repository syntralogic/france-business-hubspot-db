"""purge_all against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.ops import purge_all  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name) VALUES ('552100554','ALPHA'), ('401234562','BETA')")
        conn.execute("INSERT INTO company_addresses (company_id, is_head_office, city) SELECT company_id, TRUE, 'LYON' FROM companies")
        conn.execute("INSERT INTO api_usage (provider, calls, credits) VALUES ('firecrawl', 3, 3)")
        conn.execute("INSERT INTO import_export_batches (batch_type, status) VALUES ('import','success')")
    yield


def q(sql):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql).fetchall()


def test_dry_run_counts_and_deletes_nothing(db):
    res = purge_all.purge(DSN)
    assert res["deleted"] is False and res["tables"]["companies"] == 2 and res["tables"]["company_addresses"] == 2
    assert q("SELECT count(*) FROM companies") == [(2,)]


def test_confirmed_purge_empties_companies_and_dependents_and_keeps_the_rest(db):
    res = purge_all.purge(DSN, confirm=True)
    assert res["deleted"] is True
    assert q("SELECT count(*) FROM companies") == [(0,)] and q("SELECT count(*) FROM company_addresses") == [(0,)]
    assert q("SELECT count(*) FROM data_sources")[0][0] > 0                      # reference data kept
    assert q("SELECT count(*) FROM import_export_batches") == [(1,)]              # history kept
    assert q("SELECT credits FROM api_usage") == [(3,)]                           # today's spend still counts against the cap
    with psycopg.connect(DSN, autocommit=True) as conn:                           # ids restart
        conn.execute("INSERT INTO companies (siren, legal_name) VALUES ('552100554','ALPHA')")
    assert q("SELECT company_id FROM companies") == [(1,)]
