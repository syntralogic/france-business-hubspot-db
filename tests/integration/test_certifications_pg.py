"""Certification file loader, load records, conflicts and status view against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import ademe_rge, certifications  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B, C = "552100554", "732829320", "404833048"
A_SIRET = "55210055400013"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES (%s,'ALPHA',TRUE,'active'),"
                     "(%s,'BETA',TRUE,'active'),(%s,'GAMMA',TRUE,'active')", (A, B, C))
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office) SELECT company_id, %s, TRUE FROM companies WHERE siren=%s",
                     (A_SIRET, A))


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def write(tmp_path, body, name="certs.csv"):
    f = tmp_path / name
    f.write_text("siret,siren,cert_type,label,valid_from,valid_to\n" + body, encoding="utf-8")
    return str(f)


def load(tmp_path, body, **kw):
    return certifications.load(DSN, write(tmp_path, body), "CT-2026-01", **kw)


def test_load_is_recorded_rows_point_to_it_and_generic_source_is_used_for_insurance(db, tmp_path):
    body = (f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2999-01-01\n"
            f",{B},RC pro,,2025-06-01,2999-06-01\n"
            f",{A},Qualifelec,,,\n"
            ",123456789,Qualibat,X,,\n"                 # Luhn-invalid SIREN: rejected
            f",{A},Mystery label,,,\n")                # unknown type: rejected, never guessed, and not counted as a type the file carried
    s = load(tmp_path, body, provider="Qualibat directory extract", coverage_note="departments 69, 38")
    assert s["inserted"] == 3 and s["rejected"] == 2
    loads = q("SELECT provider, cert_types, file_name, contract_ref, coverage_note, rows_read, rows_added, rows_rejected FROM certification_loads")
    assert loads == [("Qualibat directory extract", ["Qualibat", "Qualifelec", "RC pro"], "certs.csv", "CT-2026-01", "departments 69, 38", 5, 3, 2)]
    rows = q("SELECT c.cert_type, d.code, c.load_id IS NOT NULL FROM company_certifications c JOIN data_sources d USING (source_id) ORDER BY 1")
    assert rows == [("Qualibat", "qualibat", True), ("Qualifelec", "qualifelec", True), ("RC pro", "certification_file", True)]


def test_reload_adds_nothing_updates_last_seen_and_a_changed_valid_to_is_a_conflict_first_value_kept(db, tmp_path):
    load(tmp_path, f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2026-12-31\n")
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE company_certifications SET last_seen_at = now() - interval '10 days'")
        c.commit()
    s = load(tmp_path, f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2027-12-31\n")
    assert s["inserted"] == 0 and s["already_known"] == 1 and s["conflicts"] == 1
    assert q("SELECT valid_to::text FROM company_certifications") == [("2026-12-31",)]                 # first value kept
    assert q("SELECT field_name, kept_value, other_value FROM field_conflicts") == [
        ("certification:Qualibat:Plomberie:2025-01-01:valid_to", "2026-12-31", "2027-12-31")]
    assert q("SELECT last_seen_at > now() - interval '1 day' FROM company_certifications") == [(True,)]  # seen again
    load(tmp_path, f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2027-12-31\n")                           # same conflict again
    assert q("SELECT count(*) FROM field_conflicts") == [(1,)]
    assert q("SELECT count(*) FROM certification_loads") == [(3,)]


def test_status_view_found_expired_none_in_loaded_files_and_not_checked(db, tmp_path):
    load(tmp_path, f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2999-01-01\n,{B},Qualibat,Old,2010-01-01,2011-01-01\n")
    st = {(r[0], r[1]): r[2] for r in q("SELECT siren, cert_type, certification_status FROM v_certification_status")}
    assert st == {(A, "Qualibat"): "found_valid", (B, "Qualibat"): "found_not_current", (C, "Qualibat"): "none_in_loaded_files"}
    assert not any(k[1] == "Qualifelec" for k in st)                 # no file carried it: no row = not checked


def test_ademe_load_is_recorded_and_feeds_the_status_view(db, tmp_path):
    f = tmp_path / "rge.csv"
    f.write_text(f"siret;nom_qualification;date_fin\n{A_SIRET};QualiPAC;2999-01-01\n", encoding="utf-8")
    ademe_rge.run(DSN, str(f))
    assert q("SELECT provider, cert_types, file_name, rows_read, rows_added FROM certification_loads") == [("ademe_rge", ["RGE"], "rge.csv", 1, 1)]
    assert q("SELECT load_id IS NOT NULL FROM company_certifications") == [(True,)]
    assert {(r[0], r[1]) for r in q("SELECT siren, certification_status FROM v_certification_status WHERE cert_type='RGE'")} == {
        (A, "found_valid"), (B, "none_in_loaded_files"), (C, "none_in_loaded_files")}


def test_022_is_idempotent_and_backfills_rows_loaded_before(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM certification_loads")
        cid = c.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        c.execute("INSERT INTO company_certifications (company_id, cert_type, cert_label) VALUES (%s,'RC pro','x')", (cid,))
        c.execute("UPDATE company_certifications SET last_seen_at = NULL")
        for _ in range(2):
            c.execute((SQL_DIR / "022_certification_status.sql").read_text(encoding="utf-8"))
        assert c.execute("SELECT provider, cert_types FROM certification_loads").fetchall() == [("backfill", ["RC pro"])]
        assert c.execute("SELECT last_seen_at IS NOT NULL FROM company_certifications").fetchone()[0]
        assert c.execute("SELECT certification_status FROM v_certification_status WHERE siren=%s", (A,)).fetchone()[0] == "found_valid"
