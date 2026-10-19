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


def _addr(c):
    return c.execute("SELECT latitude::float, standardized_address, address_status, geocode_score::float, geocode_precision, "
                     "geocoded_at IS NULL FROM company_addresses").fetchone()


def _geocode_ok(score="0.97", rtype="housenumber"):
    out = ("id,street,postcode,city,latitude,longitude,result_label,result_score,result_status,result_type\n"
           "{eid},x,75002,Paris,48.869,2.331,1 Rue de la Paix 75002 Paris," + score + ",ok," + rtype + "\n")
    with psycopg.connect(DSN, autocommit=True) as c:
        eid = c.execute("SELECT establishment_id FROM company_addresses").fetchone()[0]
    return geocode.run(DSN, post=lambda _t: out.format(eid=eid))


def test_municipality_only_match_is_flagged_not_verified_and_stores_precision(db):
    st = _geocode_ok("0.90", "municipality")
    assert st["verified"] == 0 and st["flagged"] == 1
    with psycopg.connect(DSN) as c:
        assert _addr(c) == (None, None, "unverified", 0.9, "municipality", False)
        assert c.execute("SELECT details FROM data_quality_issues WHERE issue_code='LOW_GEOCODE_SCORE'").fetchone()[0].startswith("MUNICIPALITY_ONLY")


def test_address_change_from_sirene_clears_the_old_coordinates_and_the_row_is_geocoded_again(db):
    assert _geocode_ok()["verified"] == 1
    with psycopg.connect(DSN, autocommit=True) as c:
        assert _addr(c) == (48.869, "1 Rue de la Paix 75002 Paris", "verified", 0.97, "housenumber", False)
        c.execute("UPDATE company_addresses SET naf = '47.11F', updated_at = now()")          # other field: untouched
        assert _addr(c)[2] == "verified"
        c.execute("UPDATE company_addresses SET street = '12 avenue Foch', postal_code = '75116'")    # what the SIRENE upsert does
        assert _addr(c) == (None, None, "unverified", None, None, True)
    st = geocode.run(DSN, post=lambda _t: "id,latitude\n")                                      # row is selected again
    assert st["selected"] == 1


def test_geocoder_write_of_address_and_coordinates_together_is_not_reset_and_sourced_rows_keep_status(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE company_addresses SET street='2 rue X', latitude=48.1, longitude=2.1, address_status='sourced'")
        assert c.execute("SELECT latitude::float, address_status FROM company_addresses").fetchone() == (48.1, "sourced")
        c.execute("UPDATE company_addresses SET street='3 rue Y'")             # not geocoded by us: nothing to invalidate
        assert c.execute("SELECT latitude::float, address_status FROM company_addresses").fetchone() == (48.1, "sourced")


def test_007_is_idempotent(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        for _ in range(2):
            c.execute((SQL_DIR / "007_geocoding.sql").read_text(encoding="utf-8"))
