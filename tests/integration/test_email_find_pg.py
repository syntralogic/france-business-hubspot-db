"""Hunter Email Finder against a REAL PostgreSQL: selection rules, storage rules, memory of attempts, opposition."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.common import provenance as pv  # noqa: E402
from fbdb.enrich import email_find as ef  # noqa: E402
from fbdb.enrich import interlocutors as il  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class Fake:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def find(self, first, last, domain):
        self.calls.append((first, last, domain))
        a = self.answers.get(last)
        if isinstance(a, Exception):
            raise a
        return a


def found(email, status="valid", score=90):
    return {"email": email, "score": score, "status": status, "sources": 2}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = conn.execute("INSERT INTO companies (siren, legal_name, status, is_diffusible) VALUES ('552100554','ALPHA','active',TRUE) RETURNING company_id").fetchone()[0]
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status, match_proof) "
                     "VALUES (%s,'website','alpha.fr','alpha.fr',TRUE,'verified','SIREN on mentions-legales')", (cid,))
    yield cid


def person(cid, last, first="Luc", function="Gérant"):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, cid, last, first, function)
        conn.commit()
    return pid


def contacts():
    with psycopg.connect(DSN) as conn:
        return conn.execute("SELECT value_normalized, value_status, interlocutor_id IS NOT NULL, attribution_level, contact_use, verify_result "
                            "FROM company_contacts WHERE contact_type='email' ORDER BY value_normalized").fetchall()


def attempts():
    with psycopg.connect(DSN) as conn:
        return dict(conn.execute("SELECT i.last_name, a.outcome FROM email_find_attempts a JOIN company_interlocutors i USING (interlocutor_id)").fetchall())


def test_valid_address_is_verified_linked_and_attributed_by_provider(db):
    person(db, "Martin")
    c = Fake({"Martin": found("Luc.Martin@alpha.fr")})
    s = ef.run(DSN, client=c)
    assert s["found"] == 1 and c.calls == [("Luc", "Martin", "alpha.fr")]
    assert contacts() == [("luc.martin@alpha.fr", "verified", True, "provider_attributed", "direction", "valid")]


def test_uncertain_verification_is_stored_as_inferred_not_exportable(db):
    person(db, "Martin")
    ef.run(DSN, client=Fake({"Martin": found("luc@alpha.fr", status="accept_all")}))
    assert contacts()[0][:2] == ("luc@alpha.fr", "inferred")
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM v_hubspot_contacts").fetchone()[0] == 0     # a hypothesis is not exported


def test_invalid_is_not_stored_and_no_answer_is_remembered(db):
    person(db, "Martin"); person(db, "Durand", "Eva")
    s = ef.run(DSN, client=Fake({"Martin": found("x@alpha.fr", status="invalid"), "Durand": None}))
    assert s["invalid"] == 1 and s["not_found"] == 1 and contacts() == []
    assert attempts() == {"Martin": "invalid", "Durand": "not_found"}
    c2 = Fake({})
    assert ef.run(DSN, client=c2)["read"] == 0 and c2.calls == []                              # not paid for again before the refresh delay


def test_only_directors_with_a_proven_domain_and_no_email_are_searched(db):
    person(db, "Martin")                                                                       # director
    person(db, "Durand", "Eva", "Responsable commercial")                                     # sales: not in the default categories
    person(db, "Petit", None, "Gérant")                                                        # no first name
    with psycopg.connect(DSN) as conn:
        other = conn.execute("INSERT INTO companies (siren, legal_name, status, is_diffusible) VALUES ('732829320','BETA','active',TRUE) RETURNING company_id").fetchone()[0]
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,'website','beta.fr','beta.fr','sourced')", (other,))
        conn.commit()
    person(other, "Noproof")                                                                   # website without SIREN proof
    c = Fake({})
    ef.run(DSN, client=c)
    assert c.calls == [("Luc", "Martin", "alpha.fr")]
    c2 = Fake({})
    ef.run(DSN, client=c2, categories=("sales",))
    assert c2.calls == [("Eva", "Durand", "alpha.fr")]


def test_person_who_already_has_an_email_is_skipped(db):
    pid = person(db, "Martin")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,'email','l@alpha.fr','l@alpha.fr','sourced')", (db,))
        il.link_contact(cur, db, pid, "email", "l@alpha.fr", "published_with_name")
        conn.commit()
    c = Fake({})
    ef.run(DSN, client=c)
    assert c.calls == []


def test_opposed_address_is_never_stored(db):
    person(db, "Martin")
    with psycopg.connect(DSN) as conn:
        pv.set_opposition(conn, "luc@alpha.fr")
        conn.commit()
    s = ef.run(DSN, client=Fake({"Martin": found("luc@alpha.fr")}))
    assert s["opposed"] == 1 and contacts() == []


def test_address_the_company_already_held_is_linked_not_duplicated(db):
    person(db, "Martin")
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,'email','luc@alpha.fr','luc@alpha.fr','sourced')", (db,))
        conn.commit()
    s = ef.run(DSN, client=Fake({"Martin": found("luc@alpha.fr")}))
    assert s["already_known"] == 1
    assert contacts() == [("luc@alpha.fr", "sourced", True, "provider_attributed", "direction", None)]


def test_quota_stops_and_does_not_charge_the_cap(db):
    person(db, "Martin")
    s = ef.run(DSN, client=Fake({"Martin": ef.QuotaExhausted("no credits")}))
    assert s["stopped_no_credits"] and attempts() == {}
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT coalesce(sum(credits),0) FROM api_usage WHERE provider='hunter_finder'").fetchone()[0] == 0


def test_daily_cap_is_respected(db):
    person(db, "Martin"); person(db, "Durand", "Eva")
    c = Fake({})
    s = ef.run(DSN, client=c, cap=1)
    assert s["cap_reached"] and len(c.calls) == 1
