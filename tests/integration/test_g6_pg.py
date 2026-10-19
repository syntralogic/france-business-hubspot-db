"""Provider-declared mailbox function and RGE certificate link against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import ademe_rge, licensed_contacts  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SIRET = "55210055400005"


@pytest.fixture()
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            c.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = c.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES ('552100554','ALPHA',TRUE,'active') "
                        "RETURNING company_id").fetchone()[0]
        c.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, status) VALUES (%s,%s,TRUE,'active')", (cid, SIRET))
        yield c


def test_provider_declared_function_is_stored_with_its_basis(conn, tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("siret,Mail devis,Mail direction,Mail autre\n"
                 f"{SIRET},devis@alpha.fr,pdg@alpha.fr,contact@alpha.fr\n", encoding="utf-8")
    m = {"emails": ["Mail devis", "Mail direction", "Mail autre"], "email_uses": {"Mail direction": "direction", "Mail autre": "nonsense"}}
    s = licensed_contacts.run(DSN, f, "TEST-1", m, reject_dir=tmp_path)
    assert s["contacts_added"] == 3 and s["rejected"] == 1                          # the invalid use is reported, the email still loaded
    rows = dict((e, (u, b)) for e, u, b in conn.execute(
        "SELECT value_normalized, contact_use, contact_use_basis FROM company_contacts WHERE contact_type='email'"))
    assert rows["pdg@alpha.fr"] == ("direction", "provider_file")                   # declared by the provider
    assert rows["devis@alpha.fr"][1] == "address_name"                              # still read from the address when no declaration
    # a later file declares the function of an email known only by its address: provider declaration replaces the address guess
    f.write_text("siret,Mail devis\n" f"{SIRET},devis@alpha.fr\n", encoding="utf-8")
    licensed_contacts.run(DSN, f, "TEST-1", {"emails": ["Mail devis"], "email_uses": {"Mail devis": "after_sales"}}, reject_dir=tmp_path)
    assert conn.execute("SELECT contact_use, contact_use_basis FROM company_contacts WHERE value_normalized='devis@alpha.fr'").fetchone() == ("after_sales", "provider_file")
    # a person's choice is never replaced
    conn.execute("UPDATE company_contacts SET contact_use='general', contact_use_basis='manual' WHERE value_normalized='devis@alpha.fr'")
    licensed_contacts.run(DSN, f, "TEST-1", {"emails": ["Mail devis"], "email_uses": {"Mail devis": "sales"}}, reject_dir=tmp_path)
    assert conn.execute("SELECT contact_use, contact_use_basis FROM company_contacts WHERE value_normalized='devis@alpha.fr'").fetchone() == ("general", "manual")


def test_rge_rows_get_their_certificate_link(conn, tmp_path):
    f = tmp_path / "rge.csv"
    f.write_text("siret;nom_qualification;date_debut;date_fin;url_qualification\n"
                 f"{SIRET};QualiPAC;2025-01-01;2099-01-01;https://annuaire.example/c/1\n"
                 f"{SIRET};Qualibois;2025-01-01;2099-01-01;ftp://bad\n", encoding="utf-8")
    ademe_rge.run(DSN, str(f), str(tmp_path / "rej.csv"))
    got = dict(conn.execute("SELECT cert_label, certificate_url FROM company_certifications WHERE cert_type='RGE'").fetchall())
    assert got == {"QualiPAC": "https://annuaire.example/c/1", "Qualibois": None}
    assert conn.execute("SELECT count(*) FROM v_company_certificates WHERE certificate_url IS NOT NULL").fetchone()[0] == 1
