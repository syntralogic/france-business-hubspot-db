"""Phone / email disagreements between sources go to field_conflicts (real PostgreSQL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import licensed_contacts as lc  # noqa: E402
from fbdb.enrich import website  # noqa: E402
from fbdb.match.resolve import contact_conflict  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A = "552100554"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES (%s,'ALPHA',TRUE,'active')", (A,))
    yield


def sid(cur, code):
    cur.execute("SELECT source_id FROM data_sources WHERE code=%s", (code,))
    return cur.fetchone()[0]


def item(norm, value=None):
    return {"normalized": norm, "value": value or norm, "url": "https://alpha.fr/contact", "context": "Tel", "use": None}


def conflicts(conn):
    return conn.execute("SELECT field_name, kept_value, other_value, kept_source_id, other_source_id FROM field_conflicts "
                        "ORDER BY conflict_id").fetchall()


def test_site_phone_vs_licensed_phone_is_kept_as_conflict(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cid = conn.execute("SELECT company_id FROM companies").fetchone()[0]
        lic, web = sid(cur, "licensed_contacts"), sid(cur, "website")
        lc.store_contact(cur, cid, None, "phone", "01 23 45 67 89", "+33123456789", "sourced", lic, None)
        website._put_contact(cur, cid, "phone", item("+33198765432"), web, "proof")
        website._put_contact(cur, cid, "phone", item("+33198765432"), web, "proof")   # re-read: no second row
        conn.commit()
        rows = conflicts(conn)
        assert rows == [("phone", "+33123456789", "+33198765432", lic, web)]
        # nothing dropped, the first value is still the primary one
        got = conn.execute("SELECT value_normalized, is_primary FROM company_contacts ORDER BY contact_id").fetchall()
        assert got == [("+33123456789", True), ("+33198765432", False)]


def test_same_source_second_number_and_first_value_are_not_conflicts(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cid = conn.execute("SELECT company_id FROM companies").fetchone()[0]
        web = sid(cur, "website")
        website._put_contact(cur, cid, "phone", item("+33123456789"), web, "p")      # first value: no primary yet
        website._put_contact(cur, cid, "phone", item("+33698765432"), web, "p")      # same site lists a mobile too
        assert conflicts(conn) == []


def test_email_conflict_and_other_types_ignored(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cid = conn.execute("SELECT company_id FROM companies").fetchone()[0]
        lic, web = sid(cur, "licensed_contacts"), sid(cur, "website")
        lc.store_contact(cur, cid, None, "email", "a@alpha.fr", "a@alpha.fr", "sourced", lic, None)
        website._put_contact(cur, cid, "email", item("b@alpha.fr"), web, "p")
        assert contact_conflict(cur, cid, "website", "https://x.fr", web) == 0
        assert [r[:3] for r in conflicts(conn)] == [("email", "a@alpha.fr", "b@alpha.fr")]
