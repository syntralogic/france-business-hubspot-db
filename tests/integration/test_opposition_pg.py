"""Per-channel, value-keyed opposition against a REAL PostgreSQL: export view, verifiers, legacy carry-over."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.common import provenance as pv  # noqa: E402
from fbdb.enrich import email_verify, phone_lookup  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SIREN = "552100554"
P1, P2, MAIL = "+33612345678", "+33698765432", "contact@alpha.fr"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s,'ALPHA SARL') RETURNING company_id", (SIREN,)).fetchone()[0]
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, city) VALUES (%s,'55210055400017',TRUE,'LYON')", (cid,))
        for ctype, val, primary in (("phone", P1, True), ("phone", P2, False), ("email", MAIL, True)):
            conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status) "
                         "VALUES (%s,%s,%s,%s,%s,'sourced')", (cid, ctype, val, val, primary))
    yield cid


def oppose(value, **kw):
    with psycopg.connect(DSN) as conn:
        return pv.set_opposition(conn, value, **kw)


def export():
    with psycopg.connect(DSN) as conn:
        return conn.execute("SELECT phone, email, phone_sms_allowed FROM v_hubspot_companies").fetchone()


def test_nothing_opposed_exports_everything(db):
    assert export() == (P1, MAIL, True)


def test_sms_opposition_keeps_the_phone_for_calls_but_flags_sms(db):
    assert oppose(P1, channels=["sms"]) == 1
    assert export() == (P1, MAIL, False)


def test_call_opposition_falls_back_to_the_next_allowed_phone(db):
    oppose(P1, channels=["call"])
    assert export()[0] == P2
    oppose(P2, channels=["call"])
    assert export() == (None, MAIL, False)


def test_email_opposition_drops_only_the_email(db):
    oppose(MAIL)
    assert export() == (P1, None, True)


def test_opposition_is_idempotent_and_reported(db):
    assert oppose(P1) == 2 and oppose(P1) == 0
    with psycopg.connect(DSN) as conn:
        assert pv.opposed_channels(conn, "06 12 34 56 78") == ["call", "sms"]
        assert pv.opposed_channels(conn, P2) == []


def test_opposition_recorded_before_we_hold_the_value_still_applies(db):
    new = "+33611112222"
    oppose(new)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE company_contacts SET is_primary = FALSE WHERE contact_type = 'phone'")
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status) "
                     "SELECT company_id, 'phone', %s, %s, TRUE, 'sourced' FROM companies", (new, new))
    assert export()[0] in (P1, P2)          # the opposed number is never chosen, though it is now the primary


def test_legacy_row_flag_is_carried_over_to_every_channel_and_stays_blocking(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE company_contacts SET opposed_at = now(), opposition_note = 'old' WHERE value_normalized IN (%s,%s)", (P1, MAIL))
        conn.execute((SQL_DIR / "003a_provenance.sql").read_text(encoding="utf-8"))     # re-apply = upgrade of an existing DB
        rows = conn.execute("SELECT contact_type, channel, note FROM contact_oppositions ORDER BY contact_type, channel").fetchall()
        assert rows == [("email", "email", "old"), ("phone", "call", "old"), ("phone", "sms", "old")]
        conn.execute((SQL_DIR / "003a_provenance.sql").read_text(encoding="utf-8"))     # idempotent
        assert conn.execute("SELECT count(*) FROM contact_oppositions").fetchone()[0] == 3
    assert export() == (P2, None, True)
    with psycopg.connect(DSN) as conn:
        assert pv.opposed_channels(conn, P1) == ["call", "sms"]


def test_verifiers_skip_a_value_only_when_every_channel_is_opposed(db):
    args = {"days": 90, "limit": 10}

    def due(sql):
        with psycopg.connect(DSN) as conn:
            return sorted(r[1] for r in conn.execute(sql, args).fetchall())

    assert due(phone_lookup.SELECT_DUE) == [P1, P2] and due(email_verify.SELECT_DUE) == [MAIL]
    oppose(P1, channels=["call"])
    assert due(phone_lookup.SELECT_DUE) == [P1, P2]           # SMS still possible: line type still useful
    oppose(P1, channels=["sms"])
    assert due(phone_lookup.SELECT_DUE) == [P2]
    oppose(MAIL)
    assert due(email_verify.SELECT_DUE) == []
