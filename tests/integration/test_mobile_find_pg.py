"""Direct mobile from a licensed provider against a REAL PostgreSQL: selection, storage, identity, do-not-call, opposition, memory, cap."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import interlocutors as il  # noqa: E402
from fbdb.enrich import mobile_find as mf  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
REF = "LUSHA-2026-01"


class Fake:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def find(self, first, last, domain):
        self.calls.append((first, last, domain))
        a = self.answers.get(last)
        if isinstance(a, Exception):
            raise a
        return a


def phone(number, type_="mobile", dnc=False):
    return {"number": number, "type": type_, "do_not_call": dnc, "updated": "2026-02-01"}


def ans(*phones, domain="alpha.fr"):
    return {"phones": list(phones), "company_domain": domain, "charged": True}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = conn.execute("INSERT INTO companies (siren, legal_name, status, is_diffusible) VALUES ('552100554','ALPHA','active',TRUE) RETURNING company_id").fetchone()[0]
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status, match_proof) "
                     "VALUES (%s,'website','alpha.fr','alpha.fr',TRUE,'verified','SIREN on mentions-legales')", (cid,))
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status) "
                     "VALUES (%s,'phone','04 72 00 00 00','+33472000000',TRUE,'sourced')", (cid,))
    yield cid


def person(cid, last, first="Luc", function="Gérant"):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pid = il.upsert_interlocutor(cur, cid, last, first, function)
        conn.commit()
    return pid


def phones():
    with psycopg.connect(DSN) as conn:
        return conn.execute("SELECT value_normalized, value_status, is_primary, interlocutor_id IS NOT NULL, attribution_level, contact_use, match_proof "
                            "FROM company_contacts WHERE contact_type='phone' AND value_normalized <> '+33472000000' ORDER BY value_normalized").fetchall()


def attempts():
    with psycopg.connect(DSN) as conn:
        return dict(conn.execute("SELECT i.last_name, a.outcome FROM mobile_find_attempts a JOIN company_interlocutors i USING (interlocutor_id)").fetchall())


def run(c, **kw):
    return mf.run(DSN, client=c, contract_ref=REF, **kw)


def test_mobile_is_stored_sourced_attributed_by_provider_never_primary_and_never_the_company_number(db):
    person(db, "Martin")
    c = Fake({"Martin": ans(phone("+33 6 12 34 56 78"), phone("+33472000001", "phone"))})
    assert run(c)["found"] == 1 and c.calls == [("Luc", "Martin", "alpha.fr")]
    (row,) = phones()
    assert row[:6] == ("+33612345678", "sourced", False, True, "provider_attributed", "direction")
    assert "Lusha person lookup" in row[6] and REF in row[6] and "alpha.fr" in row[6] and "not checked by us" in row[6]
    assert attempts() == {"Martin": "found"}
    with psycopg.connect(DSN) as conn:                                   # the company's own primary phone is untouched
        assert conn.execute("SELECT value_normalized FROM company_contacts WHERE contact_type='phone' AND is_primary").fetchall() == [("+33472000000",)]


def test_a_mobile_is_preferred_to_a_direct_line_and_a_generic_phone_is_ignored(db):
    person(db, "Martin")
    run(Fake({"Martin": ans(phone("+33472000009", "direct"), phone("+33612345678", "mobile"), phone("+33472000008", "phone"))}))
    assert [r[0] for r in phones()] == ["+33612345678"]
    person(db, "Durand")
    assert run(Fake({"Durand": ans(phone("+33472000007", "phone"))}))["no_mobile"] == 1 and attempts()["Durand"] == "no_mobile"


def test_do_not_call_opposed_and_invalid_numbers_are_never_stored(db):
    for last in ("Dnc", "Opp", "Bad"):
        person(db, last)
    with psycopg.connect(DSN, autocommit=True) as conn:
        for ch in ("call", "sms"):
            conn.execute("INSERT INTO contact_oppositions (contact_type, value_normalized, channel) VALUES ('phone','+33688888888',%s)", (ch,))
    c = Fake({"Dnc": ans(phone("+33611111111", dnc=True)), "Opp": ans(phone("+33688888888")), "Bad": ans(phone("12345"))})
    s = run(c)
    assert (s["do_not_call"], s["opposed"], s["invalid"]) == (1, 1, 1) and phones() == []
    assert attempts() == {"Dnc": "do_not_call", "Opp": "opposed", "Bad": "invalid"}


def test_opposed_on_one_channel_only_is_still_stored(db):
    person(db, "Martin")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO contact_oppositions (contact_type, value_normalized, channel) VALUES ('phone','+33612345678','sms')")
    assert run(Fake({"Martin": ans(phone("+33612345678"))}))["found"] == 1       # the export checks each channel; only a total opposition blocks it here


def test_an_answer_for_another_company_is_dropped(db):
    person(db, "Martin")
    assert run(Fake({"Martin": ans(phone("+33612345678"), domain="autre.com")}))["other_company"] == 1
    assert phones() == [] and attempts() == {"Martin": "other_company"}


def test_only_directors_with_a_proven_domain_and_no_mobile_are_searched(db):
    person(db, "Martin")                                           # director: searched
    person(db, "Petit", function="Technicien")                     # not a director: skipped
    with psycopg.connect(DSN, autocommit=True) as conn:
        other = conn.execute("INSERT INTO companies (siren, legal_name, status, is_diffusible) VALUES ('401234562','BETA','active',TRUE) RETURNING company_id").fetchone()[0]
    person(other, "Sansite")                                       # no proven website: skipped
    c = Fake({})
    run(c)
    assert c.calls == [("Luc", "Martin", "alpha.fr")]
    person(db, "Dupont")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:        # Dupont already has a mobile linked: skipped
        cur.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status, interlocutor_id, attribution_level) "
                    "SELECT %s,'phone','06 99 99 99 99','+33699999999','sourced',interlocutor_id,'published_with_name' FROM company_interlocutors WHERE last_name='Dupont'", (db,))
        conn.commit()
    c2 = Fake({})
    run(c2, refresh_days=0)
    assert ("Luc", "Dupont", "alpha.fr") not in c2.calls


def test_a_number_the_company_already_held_is_linked_not_duplicated(db):
    pid = person(db, "Martin")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,'phone','06 12 34 56 78','+33612345678','sourced')", (db,))
    assert run(Fake({"Martin": ans(phone("+33612345678"))}))["already_known"] == 1
    (row,) = phones()
    assert row[1:6] == ("sourced", False, True, "provider_attributed", "direction") and attempts() == {"Martin": "already_known"}


def test_memory_no_second_payment_before_the_refresh_delay(db):
    person(db, "Martin")
    c = Fake({"Martin": None})
    run(c)
    run(c)
    assert len(c.calls) == 1 and attempts() == {"Martin": "not_found"}
    run(c, refresh_days=0)
    assert len(c.calls) == 2


def test_quota_stops_and_does_not_charge_the_cap_and_an_error_is_retried(db):
    person(db, "Martin")
    s = run(Fake({"Martin": mf.QuotaExhausted("no credits")}))
    assert s["stopped_no_credits"] and attempts() == {}
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT coalesce(sum(credits),0) FROM api_usage WHERE provider='lusha'").fetchone()[0] == 0
    assert run(Fake({"Martin": RuntimeError("boom")}))["errors"] == 1 and attempts() == {}


def test_daily_cap_is_respected(db):
    for last in ("A1", "A2", "A3"):
        person(db, last)
    c = Fake({})
    s = run(c, cap=2)
    assert s["cap_reached"] and len(c.calls) == 2


def test_migration_is_idempotent_and_outcome_constrained(db):
    sql = (SQL_DIR / "031_mobile_find.sql").read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql)
        conn.execute(sql)
        pid = person(db, "Martin")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO mobile_find_attempts (interlocutor_id, outcome) VALUES (%s,'verified')", (pid,))
