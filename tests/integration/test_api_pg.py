"""Public data API against a REAL PostgreSQL (same TEST_DATABASE_URL as the other integration tests)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb import api  # noqa: E402
from fbdb.transform.normalize import is_valid_siret  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def luhn_siret(siren, nic="0001"):
    for c in range(10):
        if is_valid_siret(siren + nic + str(c)):
            return siren + nic + str(c)


A, B, C, D = "552100554", "401234562", "777888991", "111222335"


@pytest.fixture()
def db():
    api._stats_cache.update(at=0.0, value=None)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        rows = [(A, "ALPHA BOULANGERIE SARL", False, True), (B, "BETA NON DIFFUSIBLE", False, False),
                (C, "DUPONT JEAN EI", True, True), (D, "DELTA 50% SAS", False, True)]
        for s, n, sole, diff in rows:
            conn.execute("INSERT INTO companies (siren, legal_name, is_sole_proprietor, is_diffusible, status) "
                         "VALUES (%s,%s,%s,%s,'active')", (s, n, sole, diff))
            conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, city, department_code, postal_code) "
                         "SELECT company_id, %s, TRUE, 'Lyon', '69', '69001' FROM companies WHERE siren = %s",
                         (luhn_siret(s), s))
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) "
                     "SELECT company_id, 'phone', '0102030405', '+33102030405', 'sourced' FROM companies WHERE siren = %s", (A,))
    yield


def test_only_public_non_personal_companies_are_returned(db):
    r = api.search_companies(DSN, {})
    names = [x["legal_name"] for x in r["results"]]
    assert names == ["ALPHA BOULANGERIE SARL", "DELTA 50% SAS"] and r["total"] == 2   # no EI, no non-diffusible
    assert all("phone" not in x and "email" not in x and "director_last" not in x for x in r["results"])


def test_search_filters_and_wildcard_safety(db):
    assert api.search_companies(DSN, {"q": "boulang"})["total"] == 1
    assert api.search_companies(DSN, {"q": A})["total"] == 1
    assert api.search_companies(DSN, {"q": "%"})["total"] == 1          # literal %, only DELTA 50% SAS (a wildcard would give 2)
    assert api.search_companies(DSN, {"q": "50%"})["total"] == 1
    assert api.search_companies(DSN, {"dept": "75"})["total"] == 0
    assert api.search_companies(DSN, {"city": "lyon", "per_page": "1", "page": "2"})["results"][0]["legal_name"] == "DELTA 50% SAS"


def test_stats_and_read_only_connection(db):
    s = api.stats(DSN, ttl=0)
    assert s["companies"] == 2 and s["establishments"] == 2
    with api._connect(DSN) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM companies")


def test_company_detail_sections_and_privacy(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        conn.execute("INSERT INTO company_financials (company_id, fiscal_year, period_end, revenue_eur, equity_eur, cash_eur, debt_eur) "
                     "VALUES (%s,2025,'2025-12-31',1000000,250000,40000,90000)", (cid,))
        conn.execute("INSERT INTO company_certifications (company_id, cert_type, cert_label, valid_to, certificate_url) "
                     "VALUES (%s,'Qualibat','2111','2099-01-01','https://example.org/c/1')", (cid,))
        conn.execute("INSERT INTO company_activities (company_id, facet, code, label, matched_text, passage, source_url) "
                     "VALUES (%s,'trade','boulanger','Boulangerie','pain','Notre pain','https://alpha.fr/')", (cid,))
        conn.execute("INSERT INTO company_site_declarations (company_id, kind, value, number, qualifier, passage, source_url) "
                     "VALUES (%s,'team_size','12',12,'exact','equipe de 12 personnes','https://alpha.fr/')", (cid,))
        conn.execute("INSERT INTO company_relations (company_id, related_siren, related_name, role) VALUES (%s,'401234562','HOLDING','Président')", (cid,))
        conn.execute("INSERT INTO company_register_events (company_id, event_type, event_date, detail, basis, ref_key) "
                     "VALUES (%s,'new_establishment',current_date,'69001 LYON','sirene_creation_date','X')", (cid,))
        conn.execute("INSERT INTO company_directors (company_id, first_name, last_name, role, birth_year) VALUES (%s,'Anne','Martin','Gérant',1980)", (cid,))
    d = api.company_detail(DSN, A)
    assert d["directors"][0]["last_name"] == "Martin" and d["directors"][0]["role"] == "Gérant" and "birth_year" not in d["directors"][0]
    assert d["legal_name"] == "ALPHA BOULANGERIE SARL"
    assert d["financials"][0]["equity_eur"] == 250000 and d["financials"][0]["debt_eur"] == 90000
    assert d["certificates"][0]["certificate_url"] == "https://example.org/c/1" and d["certificates"][0]["has_attestation"] is False
    assert d["activities"][0]["source_url"] == "https://alpha.fr/" and d["declarations"][0]["value"] == "12"
    assert d["relations"][0]["role"] == "Président" and d["events"][0]["event_type"] == "new_establishment"
    flat = str(d)
    assert "phone" not in flat and "0102030405" not in flat and "content" not in d["certificates"][0]   # no contacts, no document bytes
    assert api.company_detail(DSN, C) is None and api.company_detail(DSN, B) is None and api.company_detail(DSN, "12") is None   # EI, non-diffusible
    assert api.DbApi(DSN).handle("/api/company", {"siren": [A]})[0] == 200
    assert api.DbApi(DSN).handle("/api/company", {"siren": ["999999999"]})[0] == 404
