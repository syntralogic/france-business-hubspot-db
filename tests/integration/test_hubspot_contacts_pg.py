"""v_hubspot_contacts against a REAL PostgreSQL: evidence, opposition per channel, inactive people, mobile/phone slots."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.common import provenance as pv  # noqa: E402
from fbdb.enrich import interlocutors as il  # noqa: E402
from fbdb.export import hubspot_contacts as hc  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
MOB, FIX, MAIL, MAIL2 = "+33698765432", "+33472000000", "luc@alpha.fr", "luc2@alpha.fr"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = conn.execute("INSERT INTO companies (siren, legal_name, status, is_diffusible) VALUES ('552100554','ALPHA','active',TRUE) RETURNING company_id").fetchone()[0]
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status) VALUES (%s,'website','alpha.fr','alpha.fr',TRUE,'verified')", (cid,))
        for ctype, val in (("phone", MOB), ("phone", FIX), ("email", MAIL), ("email", MAIL2)):
            conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,%s,%s,%s,'sourced')", (cid, ctype, val, val))
    yield cid


def rows():
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        return cur.execute("SELECT * FROM v_hubspot_contacts").fetchall()


def test_person_with_linked_values_is_exported_in_the_right_slots(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, db, "Martin", "Luc", "Gérant")
        for t, v, ev in (("phone", MOB, "published_with_name"), ("phone", FIX, "provider_attributed"), ("email", MAIL, "published_with_name")):
            il.link_contact(cur, db, pid, t, v, ev)
        conn.commit()
    (r,) = rows()
    assert (r["mobile"], r["phone"], r["email"], r["company_domain"], r["job_title"]) == (MOB, FIX, MAIL, "alpha.fr", "Gérant")
    assert (r["mobile_evidence"], r["phone_evidence"]) == ("published_with_name", "provider_attributed")


def test_best_evidence_wins_for_a_slot(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, db, "Martin", "Luc")
        il.link_contact(cur, db, pid, "email", MAIL, "provider_attributed")
        il.link_contact(cur, db, pid, "email", MAIL2, "confirmed_by_contact")
        conn.commit()
    assert rows()[0]["email"] == MAIL2


def test_opposed_values_and_people_without_usable_value_are_not_exported(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, db, "Martin", "Luc")
        il.link_contact(cur, db, pid, "phone", MOB, "published_with_name")
        il.link_contact(cur, db, pid, "email", MAIL, "published_with_name")
        pv.set_opposition(conn, MOB, channels=["call"])
        conn.commit()
        assert rows()[0]["mobile"] is None and rows()[0]["email"] == MAIL          # SMS-only opposition rules do not apply here: call is blocked
        pv.set_opposition(conn, MAIL)
        conn.commit()
    assert rows() == []                                                             # nothing usable left: the person is not exported


def test_person_without_linked_contact_or_inactive_is_not_exported(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        il.upsert_interlocutor(cur, db, "Martin", "Luc")                           # no linked value
        pid = il.upsert_interlocutor(cur, db, "Durand", "Eva")
        il.link_contact(cur, db, pid, "email", MAIL, "published_with_name")
        cur.execute("UPDATE company_interlocutors SET is_active = FALSE WHERE interlocutor_id = %s", (pid,))
        conn.commit()
    assert rows() == []


def test_full_csv_from_the_view(db, tmp_path):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, db, "Martin", "Luc", "Gérant")
        il.link_contact(cur, db, pid, "phone", MOB, "published_with_name")
        conn.commit()
    out = hc.export_contacts(rows(), tmp_path)
    assert out["exported"] == 1 and "+33698765432" in Path(out["files"][0]).read_text(encoding="utf-8")
