"""Licensed contacts loader against a REAL PostgreSQL (same TEST_DATABASE_URL as the other integration tests)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import licensed_contacts as lc  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

A, B, C = "552100554", "401234562", "777888991"


def luhn_siret(siren: str, nic: str = "0001") -> str:
    base = siren + nic
    for check in range(10):
        cand = base + str(check)
        if lc.is_valid_siret(cand):
            return cand
    raise AssertionError("no valid siret")


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        rows = [(A, "ALPHA SARL", True, "active"), (B, "BETA NON DIFF", False, "active"), (C, "CHARLIE FERMEE", True, "closed")]
        for s, n, diff, st in rows:
            conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES (%s,%s,%s,%s)", (s, n, diff, st))
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office) "
                     "SELECT company_id, %s, TRUE FROM companies WHERE siren = %s", (luhn_siret(A), A))
    yield


def q(sql, *args):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, args).fetchall()


def write(tmp_path, text):
    f = tmp_path / "feed.csv"
    f.write_text(text, encoding="utf-8-sig")
    return f


def test_load_scope_idempotency_and_rejects(db, tmp_path):
    siret = luhn_siret(A)
    f = write(tmp_path, (
        "siret;siren;phone;email;website\n"
        f"{siret};;01 23 45 67 89;Contact@Alpha.fr;https://www.alpha.fr\n"   # establishment-level
        f";{A};;;\n"                                                         # nothing to load
        f";{B};01 98 76 54 32;;\n"                                           # non-diffusible
        f";{C};01 98 76 54 33;;\n"                                           # closed
        ";999999999;01 11 22 33 44;;\n"                                      # unknown / invalid id
        f";{A};12345;;\n"                                                    # invalid phone
    ))
    st = lc.run(DSN, f, "TEST-2026", reject_dir=tmp_path)
    assert st["contacts_added"] == 3 and st["matched"] == 1
    assert q("SELECT contact_type, value_normalized, value_status, is_primary, establishment_id IS NOT NULL "
             "FROM company_contacts ORDER BY contact_type") == [
        ("email", "contact@alpha.fr", "sourced", True, True),
        ("phone", "+33123456789", "sourced", True, True),
        ("website", "alpha.fr", "sourced", True, True),
    ]
    assert q("SELECT count(*) FROM company_change_history WHERE table_name='company_contacts'")[0][0] == 3
    rejects = Path(st["reject_file"]).read_text(encoding="utf-8-sig")
    for code in ("NON_DIFFUSIBLE", "NOT_ACTIVE", "INVALID_PHONE"):
        assert code in rejects
    assert q("SELECT status, notes FROM import_export_batches WHERE batch_type='enrich'")[0][0] == "partial"
    assert "TEST-2026" in q("SELECT notes FROM import_export_batches")[0][0]

    # Re-running the same file adds nothing and writes no new history.
    st2 = lc.run(DSN, f, "TEST-2026", reject_dir=tmp_path)
    assert st2["contacts_added"] == 0 and st2["contacts_unchanged"] == 3
    assert q("SELECT count(*) FROM company_change_history WHERE table_name='company_contacts'")[0][0] == 3


def test_second_phone_is_not_primary_and_export_view_uses_it(db, tmp_path):
    siret = luhn_siret(A)
    lc.run(DSN, write(tmp_path, f"siret,phone\n{siret},01 23 45 67 89\n"), "T", reject_dir=tmp_path)
    lc.run(DSN, write(tmp_path, f"siret,phone\n{siret},01 55 55 55 55\n"), "T", reject_dir=tmp_path)
    assert q("SELECT value_normalized, is_primary FROM company_contacts ORDER BY contact_id") == [
        ("+33123456789", True), ("+33155555555", False)]
    assert q("SELECT phone, phone_status, contact_source FROM v_hubspot_companies WHERE siren = %s", A) == [
        ("+33123456789", "sourced", "licensed_contacts")]


def test_requires_contract_reference(db, tmp_path, monkeypatch):
    monkeypatch.delenv("LICENSED_CONTACTS_CONTRACT_REF", raising=False)
    with pytest.raises(lc.LicenceError):
        lc.run(DSN, write(tmp_path, "siren,phone\n"), None, reject_dir=tmp_path)
    assert q("SELECT count(*) FROM import_export_batches")[0][0] == 0
