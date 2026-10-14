"""Geocoding runner against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import geocode  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES ('552100554','ALPHA',TRUE,'active')")
        conn.execute("INSERT INTO company_addresses (company_id, siret, street, postal_code, city, is_head_office) "
                     "SELECT company_id, '55210055400013', '1 rue de la Paix', '75002', 'Paris', TRUE FROM companies")


def test_geocode_run_verified_and_flagged(db):
    out = ("id,street,postcode,city,latitude,longitude,result_label,result_score,result_status\n"
           "{eid},1 rue de la Paix,75002,Paris,48.869,2.331,1 Rue de la Paix 75002 Paris,{score},ok\n")
    for score, verified in (("0.97", 1), ("0.40", 0)):
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("UPDATE company_addresses SET geocoded_at=NULL, address_status='unverified'")
            eid = c.execute("SELECT establishment_id FROM company_addresses").fetchone()[0]
        st = geocode.run(DSN, post=lambda _t, o=out.format(eid=eid, score=score): o)
        assert st["verified"] == verified and st["flagged"] == 1 - verified
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT count(*) FROM data_quality_issues WHERE issue_code='LOW_GEOCODE_SCORE'").fetchone()[0] == 1
