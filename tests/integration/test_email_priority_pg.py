"""Mailbox function and priority email (spec section 7) against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import email_priority as ep  # noqa: E402
from fbdb.enrich import licensed_contacts as lc  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B = "552100554", "732829320"
A_SIRET = "55210055400013"


def add(conn, siren, email, use=None, basis=None, status="sourced", est=None, primary=False, verified_at=None):
    cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (siren,)).fetchone()[0]
    conn.execute("INSERT INTO company_contacts (company_id, establishment_id, contact_type, value, value_normalized, value_status, contact_use, "
                 "contact_use_basis, is_primary, last_verified_at) VALUES (%s,%s,'email',%s,%s,%s,%s,%s,%s,%s)",
                 (cid, est, email, email, status, use, basis, primary, verified_at))


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES (%s,'ALPHA',TRUE,'active'),(%s,'BETA',TRUE,'active')", (A, B))
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office) SELECT company_id, %s, TRUE FROM companies WHERE siren=%s", (A_SIRET, A))
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office) SELECT company_id, '73282932000074', TRUE FROM companies WHERE siren=%s", (B,))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def priority(siren=A):
    r = q("SELECT email, contact_use, priority_rank FROM v_priority_email WHERE siren=%s", siren)
    return r[0] if r else None


def test_priority_email_follows_the_function_rules_not_the_loading_order(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "compta@alpha.fr", "accounting", "address_name", primary=True)      # loaded first (is_primary) but accounting
        add(c, A, "jean.dupont@alpha.fr")                                             # function unknown
        add(c, A, "contact@alpha.fr", "general", "address_name")
        add(c, A, "direction@alpha.fr", "direction", "address_name")
    assert priority() == ("direction@alpha.fr", "direction", 1)
    assert q("SELECT value_normalized FROM company_contacts WHERE is_primary") == [("compta@alpha.fr",)]   # is_primary ("first loaded") untouched


def test_opposed_inferred_and_ruled_out_mailboxes_are_never_the_priority(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "direction@alpha.fr", "direction", "address_name")
        add(c, A, "ghost@alpha.fr", "direction", "address_name", status="inferred")
        add(c, A, "contact@alpha.fr", "general", "address_name")
        add(c, A, "sav@alpha.fr", "after_sales", "address_name")
        add(c, B, "compta@beta.fr", "accounting", "address_name")
        c.execute("INSERT INTO contact_oppositions (contact_type, value_normalized, channel) VALUES ('email','direction@alpha.fr','email')")
    assert priority() == ("contact@alpha.fr", "general", 3)                        # direction opposed, inferred ignored
    assert priority(B) is None                                                     # only accounting: "no priority email", not "no email"
    assert q("SELECT count(*) FROM company_contacts WHERE contact_type='email'")[0][0] == 5


def test_verified_beats_sourced_on_equal_rank_and_the_client_can_change_the_rules(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "devis@alpha.fr", "sales", "address_name", status="sourced", primary=True)
        add(c, A, "commercial@alpha.fr", "sales", "address_name", status="verified")
        add(c, A, "direction@alpha.fr", "direction", "address_name")
    assert priority()[0] == "direction@alpha.fr"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        ep.set_rule(cur, "sales", 1, True)
        ep.set_rule(cur, "direction", 2, True)
        conn.commit()
    assert priority()[0] == "commercial@alpha.fr"                                  # same rank 1: verified first
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        ep.set_rule(cur, "sales", 1, False)
        conn.commit()
    assert priority()[0] == "direction@alpha.fr"                                   # sales ruled out


def test_rules_edited_by_the_client_survive_a_schema_restart(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE email_priority_rules SET priority_rank = 7, eligible = FALSE WHERE contact_use = 'direction'")
        sql = (SQL_DIR / "003d_email_priority.sql").read_text(encoding="utf-8")
        c.execute(sql)
        c.execute(sql)
    assert q("SELECT priority_rank, eligible FROM email_priority_rules WHERE contact_use='direction'") == [(7, False)]


def test_hubspot_view_exports_the_preferred_function_but_an_establishment_email_still_comes_first(db):
    est = q("SELECT establishment_id FROM company_addresses WHERE siret=%s", A_SIRET)[0][0]
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "compta@alpha.fr", "accounting", "address_name", primary=True)
        add(c, A, "direction@alpha.fr", "direction", "address_name")
        add(c, B, "compta@beta.fr", "accounting", "address_name")
    rows = {r[0]: r[1:] for r in q("SELECT siren, email, email_use, email_is_priority FROM v_hubspot_companies")}
    assert rows[A] == ("direction@alpha.fr", "direction", True)
    assert rows[B] == ("compta@beta.fr", "accounting", False)                      # nothing better: still exported as before, flagged not priority
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "info@alpha-lyon.fr", "general", "address_name", est=est)
    assert q("SELECT email FROM v_hubspot_companies WHERE siren=%s", A) == [("info@alpha-lyon.fr",)]


def test_backfill_reads_the_address_only_and_never_touches_a_known_function(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "devis@alpha.fr")
        add(c, A, "sav@alpha.fr")
        add(c, A, "jean.dupont@alpha.fr")
        add(c, A, "contact@alpha.fr", "direction", "manual")                       # a person said direction: stays
    dry = ep.backfill_use(DSN, dry_run=True)
    assert dry["set"] == 2 and dry["unreadable"] == 1
    assert q("SELECT count(*) FROM company_contacts WHERE contact_use_basis='address_name'")[0][0] == 0
    s = ep.backfill_use(DSN)
    assert s["by_use"] == {"sales": 1, "after_sales": 1}
    rows = dict((r[0], r[1:]) for r in q("SELECT value_normalized, contact_use, contact_use_basis FROM company_contacts"))
    assert rows["devis@alpha.fr"] == ("sales", "address_name")
    assert rows["jean.dupont@alpha.fr"] == (None, None)
    assert rows["contact@alpha.fr"] == ("direction", "manual")


def test_set_use_is_manual_logged_and_never_creates_an_email(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "jean.dupont@alpha.fr")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        r = ep.set_use(cur, A, "Jean.Dupont@Alpha.fr", "direction")
        conn.commit()
        assert r["old_use"] is None and r["new_use"] == "direction"
        with pytest.raises(LookupError):
            ep.set_use(cur, A, "nobody@alpha.fr", "sales")
        with pytest.raises(ValueError):
            ep.set_use(cur, A, "jean.dupont@alpha.fr", "boss")
    assert q("SELECT contact_use, contact_use_basis FROM company_contacts") == [("direction", "manual")]
    assert q("SELECT table_name, field_name, old_value, new_value, change_type FROM company_change_history") == \
        [("company_contacts", "contact_use", None, "direction", "update")]
    assert priority() == ("jean.dupont@alpha.fr", "direction", 1)
    with psycopg.connect(DSN, autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):                         # a basis without a function is refused by the database
            c.execute("UPDATE company_contacts SET contact_use = NULL WHERE contact_use_basis = 'manual'")


def test_show_marks_the_priority_and_the_reason_others_are_not(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "sav@alpha.fr", "after_sales", "address_name")
        add(c, A, "contact@alpha.fr", "general", "address_name")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        rows = ep.show(cur, A)
    assert [(r["email"], r["is_priority"], r["eligible"]) for r in rows] == [("contact@alpha.fr", True, True), ("sav@alpha.fr", False, False)]


def test_licensed_loader_reads_the_function_from_the_address(db, tmp_path):
    f = tmp_path / "feed.csv"
    f.write_text(f"siret;siren;phone;email\n;{A};;devis@alpha.fr\n;{A};;jean.dupont@alpha.fr\n", encoding="utf-8-sig")
    lc.run(DSN, f, "CT-1", reject_dir=tmp_path)
    assert dict((r[0], r[1:]) for r in q("SELECT value_normalized, contact_use, contact_use_basis FROM company_contacts")) == \
        {"devis@alpha.fr": ("sales", "address_name"), "jean.dupont@alpha.fr": (None, None)}


def test_kpis_count_priority_emails_and_known_functions(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        add(c, A, "direction@alpha.fr", "direction", "address_name")
        add(c, A, "jean.dupont@alpha.fr")
        add(c, B, "compta@beta.fr", "accounting", "address_name")
    _, rates = metrics.collect(DSN, target_rows=10)
    assert rates["match_companies_with_priority_email"] == 0.5                     # A only
    assert round(rates["match_emails_with_known_function"], 3) == round(2 / 3, 3)
