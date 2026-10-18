"""Interlocutors against a REAL PostgreSQL: constraints, linking, directors sync, opposition-aware view."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.common import provenance as pv  # noqa: E402
from fbdb.enrich import interlocutors as il  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
P1, P2, MAIL = "+33612345678", "+33698765432", "luc@alpha.fr"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = conn.execute("INSERT INTO companies (siren, legal_name) VALUES ('552100554','ALPHA SARL') RETURNING company_id").fetchone()[0]
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, city) VALUES (%s,'55210055400017',TRUE,'LYON')", (cid,))
        for ctype, val in (("phone", P1), ("phone", P2), ("email", MAIL)):
            conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) "
                         "VALUES (%s,%s,%s,%s,'sourced')", (cid, ctype, val, val))
    yield cid


def test_two_people_each_with_own_contacts(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        boss = il.upsert_interlocutor(cur, db, "Martin", "Luc", legal_role="Gerant", operational_function="Gérant")
        sales = il.upsert_interlocutor(cur, db, "Durand", "Eva", "Responsable commercial", source_code="team_page")
        il.link_contact(cur, db, boss, "phone", P1, "published_with_name", "direction")
        il.link_contact(cur, db, boss, "email", MAIL, "provider_attributed", "direction")
        il.link_contact(cur, db, sales, "phone", P2, "published_with_name", "sales")
        conn.commit()
        cur.execute("SELECT last_name, function_category, contact_type, attribution_level FROM v_interlocutor_contacts "
                    "ORDER BY last_name, contact_type")
        assert cur.fetchall() == [("Durand", "sales", "phone", "published_with_name"),
                                  ("Martin", "director", "email", "provider_attributed"),
                                  ("Martin", "director", "phone", "published_with_name")]


def test_upsert_is_idempotent(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        a = il.upsert_interlocutor(cur, db, "Martin", "Luc", operational_function="Gérant")
        b = il.upsert_interlocutor(cur, db, "MARTIN", "luc", operational_function="gérant")
        assert a == b


def test_database_refuses_a_person_link_without_evidence(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        pid = il.upsert_interlocutor(conn.cursor(), db, "Martin", "Luc")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE company_contacts SET interlocutor_id = %s WHERE value_normalized = %s", (pid, P1))


def test_database_refuses_confirmation_without_note(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        pid = il.upsert_interlocutor(conn.cursor(), db, "Martin", "Luc")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE company_interlocutors SET decision_maker_confirmed = TRUE WHERE interlocutor_id = %s", (pid,))


def test_confirm_with_note(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, db, "Martin", "Luc")
        il.confirm_decision_maker(cur, pid, "confirmed on call 2026-10-20")
        cur.execute("SELECT decision_maker_confirmed, confirmed_at IS NOT NULL FROM company_interlocutors WHERE interlocutor_id = %s", (pid,))
        assert cur.fetchone() == (True, True)


def test_view_hides_values_opposed_on_that_channel(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, db, "Martin", "Luc")
        il.link_contact(cur, db, pid, "phone", P1, "published_with_name")
        il.link_contact(cur, db, pid, "phone", P2, "published_with_name")
        il.link_contact(cur, db, pid, "email", MAIL, "published_with_name")
        pv.set_opposition(conn, P1, channels=["call"])
        pv.set_opposition(conn, MAIL)
        conn.commit()
        cur.execute("SELECT contact_type, value FROM v_interlocutor_contacts WHERE contact_id IS NOT NULL")
        assert cur.fetchall() == [("phone", P2)]
        cur.execute("SELECT count(*) FROM v_interlocutor_contacts")      # the person itself stays listed
        assert cur.fetchone()[0] >= 1


def test_link_to_a_person_of_another_company_is_refused(db):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        other = cur.execute("INSERT INTO companies (siren, legal_name) VALUES ('732829320','BETA') RETURNING company_id").fetchone()[0]
        pid = il.upsert_interlocutor(cur, other, "Petit", "Ana")
        with pytest.raises(ValueError, match="does not belong"):
            il.link_contact(cur, db, pid, "phone", P1, "published_with_name")


def test_sync_directors_creates_deactivates_and_never_invents_contacts(db):
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO company_directors (company_id, first_name, last_name, role) VALUES (%s,'Luc','Martin','Gerant')", (db,))
        conn.execute("INSERT INTO company_directors (company_id, first_name, last_name, role) VALUES (%s,'Eva','Durand','Presidente')", (db,))
        conn.commit()
        assert il.sync_directors(conn) == {"created": 2, "refreshed": 0, "deactivated": 0}
        assert il.sync_directors(conn)["created"] == 0
        conn.execute("UPDATE company_directors SET is_active = FALSE WHERE last_name = 'Durand'")
        conn.commit()
        assert il.sync_directors(conn)["deactivated"] == 1
        rows = conn.execute("SELECT last_name, legal_role, function_category, is_active FROM company_interlocutors ORDER BY last_name").fetchall()
        assert rows == [("Durand", "Presidente", "director", False), ("Martin", "Gerant", "director", True)]
        assert conn.execute("SELECT count(*) FROM company_contacts WHERE interlocutor_id IS NOT NULL").fetchone()[0] == 0
