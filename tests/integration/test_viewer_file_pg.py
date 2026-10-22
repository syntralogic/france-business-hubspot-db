"""Single-file offline viewer export against a REAL PostgreSQL."""
import json
import os
import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.export import viewer_file  # noqa: E402
from fbdb.transform.normalize import is_valid_siret  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B, C = "552100554", "401234562", "777888991"


def luhn_siret(siren, nic="0001"):
    for c in range(10):
        if is_valid_siret(siren + nic + str(c)):
            return siren + nic + str(c)


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        for s, n, sole, diff in [(A, "ALPHA </script><b>BOULANGERIE", False, True), (B, "BETA NON DIFFUSIBLE", False, False), (C, "DUPONT EI", True, True)]:
            conn.execute("INSERT INTO companies (siren, legal_name, is_sole_proprietor, is_diffusible, status) VALUES (%s,%s,%s,%s,'active')", (s, n, sole, diff))
            conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, city, department_code, postal_code) "
                         "SELECT company_id, %s, TRUE, 'Lyon', '69', '69001' FROM companies WHERE siren = %s", (luhn_siret(s), s))
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        conn.execute("INSERT INTO company_financials (company_id, fiscal_year, period_end, revenue_eur, equity_eur) VALUES (%s,2025,'2025-12-31',1000000,250000)", (cid,))
        conn.execute("INSERT INTO company_activities (company_id, facet, code, label, matched_text, passage, source_url) "
                     "VALUES (%s,'trade','boulanger','Boulangerie','pain','Notre pain','https://alpha.fr/')", (cid,))
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,'phone','0102030405','+33102030405','sourced')", (cid,))
    yield


def test_export_is_one_safe_file_with_details_and_no_private_data(db, tmp_path):
    with psycopg.connect(DSN) as conn:
        conn.read_only = True
        data = viewer_file.collect(conn)
    html = viewer_file.render(data)
    assert [r["siren"] for r in data["rows"]] == [A]                        # no non-diffusible, no sole proprietor
    d = data["details"][A]
    assert d["financials"][0]["fiscal_year"] == 2025 and d["activities"][0]["label"] == "Boulangerie"
    assert "0102030405" not in html and "+33102030405" not in html          # no phone / email in the file
    payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "</" not in payload                                              # a company name cannot break out of the tag
    assert json.loads(payload)["rows"][0]["legal_name"] == "ALPHA </script><b>BOULANGERIE"
    (tmp_path / "v.html").write_text(html, encoding="utf-8")


def test_limit(db):
    with psycopg.connect(DSN) as conn:
        assert len(viewer_file.collect(conn, limit=1)["rows"]) == 1
