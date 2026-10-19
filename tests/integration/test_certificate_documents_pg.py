"""Certificate link and attestation documents (spec section 11) against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import certificate_documents as cd  # noqa: E402
from fbdb.enrich import certifications  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B, C, D = "552100554", "732829320", "404833048", "775670417"
A_SIRET = "55210055400013"
PDF1 = b"%PDF-1.4\nfirst attestation\n%%EOF\n"
PDF2 = b"%PDF-1.4\nsecond attestation\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 30


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES (%s,'ALPHA',TRUE,'active'),(%s,'BETA',TRUE,'active'),"
                     "(%s,'GAMMA NON DIFF',FALSE,'active'),(%s,'DELTA FERMEE',TRUE,'closed')", (A, B, C, D))
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office) SELECT company_id, %s, TRUE FROM companies WHERE siren=%s", (A_SIRET, A))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def f(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def attach(siren, ctype, path, **kw):
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        try:
            r = cd.attach(cur, siren, ctype, path, kw.pop("received_from", "Insurer X"), **kw)
        except cd.DocumentError:
            conn.rollback()
            raise
        conn.commit()
        return r


def load_certs(tmp_path, body, name="certs.csv", header="siret,siren,cert_type,label,valid_from,valid_to,certificate_url"):
    p = tmp_path / name
    p.write_text(header + "\n" + body, encoding="utf-8")
    return certifications.load(DSN, str(p), "CT-2026-01")


def test_certificate_link_is_loaded_filled_later_and_a_different_link_is_a_conflict(db, tmp_path):
    s = load_certs(tmp_path, f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2999-01-01,https://www.qualibat.com/e/1\n"
                             f",{B},Qualibat,Zinguerie,2025-01-01,2999-01-01,www.qualibat.com/e/2\n"          # not a link: row loads, link dropped
                             f",{A},RC pro,,2025-01-01,2999-01-01,\n")
    assert s["inserted"] == 3 and s["urls_added"] == 1 and s["urls_invalid"] == 1
    assert dict(q("SELECT cert_label, certificate_url FROM company_certifications WHERE cert_label IS NOT NULL")) == {
        "Plomberie": "https://www.qualibat.com/e/1", "Zinguerie": None}
    s = load_certs(tmp_path, f",{B},Qualibat,Zinguerie,2025-01-01,2999-01-01,https://www.qualibat.com/e/2\n"                   # fills the empty link
                             f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2999-01-01,https://other.example/x\n", name="again.csv")   # different link
    assert s["inserted"] == 0 and s["urls_added"] == 1 and s["conflicts"] == 1
    assert dict(q("SELECT cert_label, certificate_url FROM company_certifications WHERE cert_label IS NOT NULL")) == {
        "Plomberie": "https://www.qualibat.com/e/1", "Zinguerie": "https://www.qualibat.com/e/2"}          # first link kept
    assert q("SELECT field_name, kept_value, other_value FROM field_conflicts") == [
        ("certification:Qualibat:Plomberie:2025-01-01:certificate_url", "https://www.qualibat.com/e/1", "https://other.example/x")]
    with psycopg.connect(DSN, autocommit=True) as c, pytest.raises(psycopg.errors.CheckViolation):          # the database refuses a non-http link
        c.execute("UPDATE company_certifications SET certificate_url = 'javascript:alert(1)' WHERE cert_label = 'Plomberie'")


def test_attach_to_a_loaded_row_is_idempotent_and_a_second_file_is_a_second_document(db, tmp_path):
    load_certs(tmp_path, f",{A},RC pro,,2026-01-01,2999-01-01,\n")
    r = attach(A, "RC pro", f(tmp_path, "att.pdf", PDF1), note="signed copy")
    assert r["status"] == "added" and r["certification_created"] is False
    assert attach(A, "rc professionnelle", f(tmp_path, "copy.pdf", PDF1))["status"] == "unchanged"            # same bytes, other name, other spelling
    assert attach(A, "RC pro", f(tmp_path, "att2.pdf", PDF2))["status"] == "added"
    assert q("SELECT kind, mime_type, size_bytes, received_from FROM certification_documents ORDER BY document_id") == [
        ("attestation", "application/pdf", len(PDF1), "Insurer X"), ("attestation", "application/pdf", len(PDF2), "Insurer X")]
    assert q("SELECT documents, has_attestation FROM v_company_certificates WHERE siren=%s", A) == [(2, True)]
    assert q("SELECT attestation_status FROM v_attestation_status WHERE siren=%s", A) == [("document_current",)]


def test_a_row_is_created_only_with_an_end_date_and_is_marked_manual_and_logged(db, tmp_path):
    with pytest.raises(cd.DocumentError, match="--valid-to"):
        attach(A, "Decennale", f(tmp_path, "d.pdf", PDF1))
    assert q("SELECT count(*) FROM company_certifications") == [(0,)] and q("SELECT count(*) FROM certification_documents") == [(0,)]
    from datetime import date
    r = attach(A, "Decennale", f(tmp_path, "d.pdf", PDF1), valid_from=date(2026, 3, 1), valid_to=date(2027, 2, 28), siret=A_SIRET, label="Assurance Y")
    assert r["certification_created"] is True
    assert q("SELECT c.cert_type, c.valid_to::text, c.establishment_id IS NOT NULL, s.code, c.load_id FROM company_certifications c JOIN data_sources s USING (source_id)") == [
        ("Decennale", "2027-02-28", True, "manual", None)]
    assert q("SELECT table_name, field_name, new_value, change_type FROM company_change_history") == [("company_certifications", "cert_type", "Decennale", "insert")]


def test_ambiguous_wrong_kind_and_ineligible_companies_are_refused_with_a_reason(db, tmp_path):
    load_certs(tmp_path, f",{A},RC pro,2025,2025-01-01,2025-12-31,\n,{A},RC pro,2026,2026-01-01,2026-12-31,\n,{A},Qualibat,Plomberie,2025-01-01,2999-01-01,\n")
    with pytest.raises(cd.DocumentError, match="2 certification rows match"):
        attach(A, "RC pro", f(tmp_path, "x.pdf", PDF1))
    assert attach(A, "RC pro", f(tmp_path, "x.pdf", PDF1), label="2026")["status"] == "added"                 # narrowed by label
    with pytest.raises(cd.DocumentError, match="attestation is for"):
        attach(A, "Qualibat", f(tmp_path, "q.pdf", PDF1), kind="attestation")
    assert attach(A, "Qualibat", f(tmp_path, "q.pdf", PDF1))["status"] == "added"                             # a copy of a certificate is fine
    assert q("SELECT kind FROM certification_documents ORDER BY document_id") == [("attestation",), ("certificate",)]
    for siren in (C, D, "999999999"):
        with pytest.raises(cd.DocumentError):
            attach(siren, "RC pro", f(tmp_path, "n.pdf", PDF1), valid_to=__import__("datetime").date(2999, 1, 1))
    with pytest.raises(cd.DocumentError, match="received_from"):
        attach(A, "RC pro", f(tmp_path, "x.pdf", PDF1), received_from=" ", label="2026")


def test_attestation_status_is_honest_about_expired_unknown_end_and_missing_documents(db, tmp_path):
    load_certs(tmp_path, f",{A},RC pro,,2020-01-01,2021-01-01,\n"          # expired, will get a document
                         f",{B},RC pro,,,,\n"                              # no end date, will get a document
                         f",{B},Decennale,,2025-01-01,2999-01-01,\n")      # no document
    attach(A, "RC pro", f(tmp_path, "a.pdf", PDF1))
    attach(B, "RC pro", f(tmp_path, "b.pdf", PDF2))
    st = {(r[0], r[1]): r[2] for r in q("SELECT siren, cert_type, attestation_status FROM v_attestation_status")}
    assert st == {(A, "RC pro"): "document_expired", (B, "RC pro"): "document_end_date_unknown", (B, "Decennale"): "no_document"}
    assert not any(k[0] == C for k in st)                                    # no row of that type: no status, never "uninsured"


def test_stored_file_comes_back_identical_and_a_tampered_one_is_not_written(db, tmp_path):
    load_certs(tmp_path, f",{A},RC pro,,2026-01-01,2999-01-01,\n")
    r = attach(A, "RC pro", f(tmp_path, "att.pdf", PDF1))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        out = tmp_path / "back.pdf"
        assert cd.get_document(cur, r["document_id"], out)["bytes"] == len(PDF1) and out.read_bytes() == PDF1
        with pytest.raises(LookupError):
            cd.get_document(cur, 999, out)
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE certification_documents SET content = %s", (b"%PDF-1.4\nforged attestation!!!\n%%EOF\n"[:len(PDF1)].ljust(len(PDF1), b"x"),))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur, pytest.raises(cd.DocumentError, match="SHA-256"):
        cd.get_document(cur, r["document_id"], tmp_path / "bad.pdf")
    assert not (tmp_path / "bad.pdf").exists()


def test_views_never_expose_the_file(db):
    for view in ("v_company_certificates", "v_attestation_status"):
        cols = {r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name=%s", view)}
        assert cols and "content" not in cols


def test_manifest_loads_good_lines_and_reports_the_bad_ones(db, tmp_path):
    f(tmp_path, "ok.pdf", PDF1)
    f(tmp_path, "scan.png", PNG)
    f(tmp_path, "evil.pdf", b"MZ not a pdf")
    (tmp_path / "manifest.csv").write_text(
        "siren,cert_type,file,valid_from,valid_to,label,kind,note\n"
        f"{A},RC pro,ok.pdf,2026-01-01,2027-01-01,,,\n"                      # creates the row (end date given)
        f"{B},Decennale,scan.png,01/03/2026,28/02/2027,,,\n"                  # PNG scan, DD/MM/YYYY
        f"{A},RC pro,evil.pdf,2026-01-01,2027-01-01,,,\n"                     # not a PDF
        f"{A},RC pro,missing.pdf,2026-01-01,2027-01-01,,,\n"                  # no such file
        f"{C},RC pro,ok.pdf,2026-01-01,2027-01-01,,,\n"                       # non-diffusible company
        f"{B},ISO9001,ok.pdf,2026-01-01,2027-01-01,,,\n"                      # unknown type, never guessed
        f"{B},RC pro,ok.pdf,31/31/2026,,,,\n", encoding="utf-8")              # unreadable date
    s = cd.load_manifest(DSN, tmp_path / "manifest.csv", "Client batch Oct 2026")
    assert (s["rows"], s["added"], s["rejected"], s["certifications_created"]) == (7, 2, 5, 2)
    rej = (tmp_path / "manifest.rejects.csv").read_text(encoding="utf-8")
    for word in ("not a PDF", "not found", "not an active", "unknown certification type", "unreadable date"):
        assert word in rej
    assert q("SELECT mime_type, received_from FROM certification_documents ORDER BY document_id") == [
        ("application/pdf", "Client batch Oct 2026"), ("image/png", "Client batch Oct 2026")]
    s2 = cd.load_manifest(DSN, tmp_path / "manifest.csv", "Client batch Oct 2026")                       # run again: nothing new
    assert s2["added"] == 0 and s2["unchanged"] == 2


def test_026_is_idempotent_and_the_kpis_count_links_and_dated_attestations(db, tmp_path):
    with psycopg.connect(DSN, autocommit=True) as c:
        sql = (SQL_DIR / "026_certificate_documents.sql").read_text(encoding="utf-8")
        c.execute(sql)
        c.execute(sql)
    load_certs(tmp_path, f"{A_SIRET},,Qualibat,Plomberie,2025-01-01,2999-01-01,https://www.qualibat.com/e/1\n,{A},RC pro,,2026-01-01,2999-01-01,\n,{B},RC pro,,2026-01-01,2999-01-01,\n")
    attach(A, "RC pro", f(tmp_path, "a.pdf", PDF1))
    _, rates = metrics.collect(DSN, target_rows=10)
    assert rates["match_companies_with_certificate_link"] == 0.5 and rates["match_companies_with_attestation_in_date"] == 0.5
