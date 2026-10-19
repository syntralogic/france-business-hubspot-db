"""Places loader against a REAL PostgreSQL (TEST_DATABASE_URL), API stubbed."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.common import usage  # noqa: E402
from fbdb.enrich import places  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
HERE = {"latitude": 48.869, "longitude": 2.331}


class Fake:
    def __init__(self):
        self.queries = []

    def search(self, query, lat, lon):
        self.queries.append(query)
        if "ALPHA" in query:
            return [{"id": "PL1", "displayName": {"text": "Alpha"}, "location": HERE, "rating": 4.6,
                     "userRatingCount": 120, "googleMapsUri": "https://maps.google.com/?cid=1"}]
        if "BETA" in query:
            return [{"id": "PL2", "displayName": {"text": "Totally Different"}, "location": HERE, "rating": 3.0, "userRatingCount": 2}]
        return []


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("""INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES
          ('552100554','ALPHA',TRUE,'active'),('732829320','BETA',TRUE,'active'),('404833048','GAMMA',TRUE,'active'),
          ('111111118','HIDDEN',FALSE,'active'),('222222226','UNVERIFIED',TRUE,'active')""")
        conn.execute("""INSERT INTO company_addresses (company_id, siret, is_head_office, street, postal_code, city, naf,
                          address_status, latitude, longitude)
                        SELECT company_id, siren || '00013', TRUE, '1 rue de la Paix', '75002', 'Paris', '47.11F',
                          CASE WHEN siren='222222226' THEN 'unverified' ELSE 'verified' END, 48.869, 2.331 FROM companies""")


def test_selection_matching_cap_and_refresh(db):
    fake = Fake()
    s = places.run(DSN, fake, limit=10, cap=3, naf_prefixes=["47"])
    assert s["read"] == 3 and s["matched"] == 1 and s["low_confidence_or_none"] == 2 and s["calls"] == 3
    assert s["est_cost_usd"] == round(3 * places.COST_PER_CALL, 4)
    assert not any("HIDDEN" in q or "UNVERIFIED" in q for q in fake.queries)
    with psycopg.connect(DSN) as c:
        rows = {r[0]: r[1:] for r in c.execute(
            "SELECT s.siren, r.provider_place_id, r.rating::float, r.match_confidence >= 0.8 FROM company_reviews r "
            "JOIN company_addresses a USING (establishment_id) JOIN companies s USING (company_id)")}
        assert rows["552100554"] == ("PL1", 4.6, True) and rows["732829320"][2] is False and rows["404833048"][0] is None
        assert usage.used_today(c, "google_places") == (3, 3)
    s2 = places.run(DSN, fake, limit=10, cap=3)                 # all checked < 30 days ago: nothing queried
    assert s2["read"] == 0
    s3 = places.run(DSN, fake, limit=10, cap=3, naf_prefixes=["99"])
    assert s3["read"] == 0                                      # NAF filter honoured
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE company_reviews SET collected_at = now() - interval '40 days', last_attempt_at = now() - interval '40 days'")
        c.commit()
    s4 = places.run(DSN, fake, limit=10, cap=3)                 # stale rows due again but today's cap (3) is already used
    assert s4["read"] == 0 and s4["cap_reached"]


class Flaky:
    """ALPHA works the first time and fails after; GAMMA always fails; BETA always fails with a quota error when asked to."""
    def __init__(self):
        self.fail = set()

    def search(self, query, lat, lon):
        for name in self.fail:
            if name in query:
                raise RuntimeError("HTTP 503")
        return Fake().search(query, lat, lon)


def _status(c):
    return dict(c.execute("SELECT s.siren, r.status FROM company_reviews r JOIN company_addresses a USING (establishment_id) "
                          "JOIN companies s USING (company_id)").fetchall())


def test_status_matched_to_verify_not_found_and_not_searched(db):
    places.run(DSN, Fake(), limit=10, cap=10)
    with psycopg.connect(DSN) as c:
        assert _status(c) == {"552100554": "matched", "732829320": "to_verify", "404833048": "not_found"}
        # UNVERIFIED address and HIDDEN company were never searched: no row = "not searched"
        assert c.execute("SELECT count(*) FROM company_reviews").fetchone()[0] == 3


def test_failed_call_is_inaccessible_retried_after_7_days_and_never_erases_a_stored_rating(db):
    fl = Flaky()
    places.run(DSN, fl, limit=10, cap=50)                       # everything works once
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE company_reviews SET collected_at = now() - interval '40 days', last_attempt_at = now() - interval '40 days'")
        c.commit()
    fl.fail = {"ALPHA", "GAMMA"}
    s = places.run(DSN, fl, limit=10, cap=50)
    assert s["errors"] == 2
    with psycopg.connect(DSN) as c:
        st = _status(c)
        assert st["552100554"] == "matched"                      # had a rating: status and rating kept
        assert c.execute("SELECT rating::float FROM company_reviews WHERE provider_place_id='PL1'").fetchone()[0] == 4.6
        assert st["404833048"] == "inaccessible"                 # had nothing: now says why
        assert c.execute("SELECT error_detail FROM company_reviews WHERE status='inaccessible'").fetchone()[0] == "HTTP 503"
    again = places.run(DSN, fl, limit=10, cap=50)
    assert again["read"] == 0 and again["errors"] == 0           # both failed today: neither is asked again before 7 days
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT count(*) FROM company_reviews WHERE status='inaccessible'").fetchone()[0] == 1


def test_inaccessible_waits_retry_days_then_is_asked_again(db):
    fl = Flaky()
    fl.fail = {"GAMMA"}
    places.run(DSN, fl, limit=10, cap=50)
    fl.fail = set()
    assert places.run(DSN, fl, limit=10, cap=50)["read"] == 0   # failed today: not asked again before 7 days
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE company_reviews SET last_attempt_at = now() - interval '8 days' WHERE status='inaccessible'")
        c.commit()
    assert places.run(DSN, fl, limit=10, cap=50)["read"] == 1
    with psycopg.connect(DSN) as c:
        assert _status(c)["404833048"] == "not_found"
        assert c.execute("SELECT error_detail FROM company_reviews WHERE status='not_found'").fetchone()[0] is None


def test_021_idempotent_and_backfills_old_rows(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        eid = c.execute("SELECT establishment_id FROM company_addresses LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO company_reviews (establishment_id, provider, provider_place_id, match_confidence) VALUES (%s,'google_places','X',0.5)", (eid,))
        c.execute("UPDATE company_reviews SET status = NULL")
        for _ in range(2):
            c.execute((SQL_DIR / "021_places_status.sql").read_text(encoding="utf-8"))
        assert c.execute("SELECT status FROM company_reviews").fetchone()[0] == "to_verify"
