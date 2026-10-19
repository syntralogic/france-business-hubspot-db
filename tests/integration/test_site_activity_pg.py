"""Site activity against a REAL PostgreSQL (TEST_DATABASE_URL), with a fake Firecrawl client (no network)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import site_activity as sa  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B, C = "552100554", "401234562", "552120222"
PROOF = "SIREN 552 100 554 on https://alpha.fr/mentions-legales"


class Fake:
    def __init__(self, pages):
        self.pages, self.scrapes = pages, []

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        for siren, name in ((A, "ALPHA ELEC"), (B, "BETA SARL"), (C, "GAMMA SAS")):
            conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s,%s)", (siren, name))
        # A: site proven by SIREN. B: a site that is NOT proven (no match_proof). C: no site at all.
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status, match_proof, is_primary)
                        SELECT company_id, 'website', 'alpha.fr', 'alpha.fr', 'verified', %s, TRUE FROM companies WHERE siren = %s""", (PROOF, A))
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status)
                        SELECT company_id, 'website', 'beta.fr', 'beta.fr', 'sourced' FROM companies WHERE siren = %s""", (B,))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


HOME = "https://alpha.fr"
PAGES = {
    HOME: {"markdown": "# Alpha Elec\nÉlectricien à Lyon pour particuliers.\n![Borne IRVE posée chez un client](irve.jpg)",
           "links": ["/services", "/blog/borne-irve", "/mentions-legales"]},
    HOME + "/services": {"markdown": "Bornes de recharge IRVE, climatisation. Dépannage 24h/24. Installateur agréé Daikin.", "links": []},
}


def test_only_a_site_proven_by_siren_is_read_and_every_claim_keeps_passage_url_and_proof(db):
    c = Fake(PAGES)
    stats = sa.run(DSN, client=c, limit=10)
    assert stats["read"] == 1 and stats["extracted"] == 1 and c.scrapes == [HOME, HOME + "/services"]    # B (unproven) and C (no site) untouched
    rows = {(r[0], r[1]): r[2:] for r in q("""SELECT facet, code, passage, source_url, matched_text, site_proof FROM company_activities""")}
    assert ("trade", "electricien") in rows and rows[("trade", "electricien")][1] == HOME
    irve = rows[("specialty", "irve")]
    assert irve[1] == HOME + "/services" and "Bornes de recharge IRVE" in irve[0] and irve[3] == PROOF
    assert rows[("approval", "brand_agreement:daikin")][1] == HOME + "/services"
    # the photo of a charging point on the home page is NOT a claim on its own page: the claim comes from the services text only
    assert rows[("specialty", "irve")][1] != HOME
    # run memory + date
    assert q("SELECT outcome, pages_read, items_found > 0 FROM activity_runs") == [("extracted", 2, True)]
    assert q("SELECT count(*) FROM company_activities WHERE company_id NOT IN (SELECT company_id FROM companies WHERE siren=%s)", A)[0][0] == 0
    # claimed approvals never become official certifications
    assert q("SELECT count(*) FROM company_certifications")[0][0] == 0


def test_not_read_twice_before_the_refresh_delay_and_nothing_is_deleted_on_refresh(db):
    c = Fake(PAGES)
    sa.run(DSN, client=c, limit=10)
    n = len(c.scrapes)
    assert sa.run(DSN, client=c, limit=10)["read"] == 0 and len(c.scrapes) == n          # remembered: no new credit
    first = q("SELECT code, passage, source_url, collected_at FROM company_activities ORDER BY code")
    # refresh: the site now says something different and no longer mentions the climatisation
    changed = Fake({HOME: {"markdown": "Plombier chauffagiste. Électricien: nous posons des alarmes.", "links": []}})
    s = sa.run(DSN, client=changed, limit=10, refresh_days=0)
    assert s["read"] == 1 and s["claims_added"] >= 2
    now = {r[0]: r for r in q("SELECT code, passage, source_url, collected_at FROM company_activities ORDER BY code")}
    for row in first:                                                                   # nothing deleted, first passage / URL kept
        assert now[row[0]][1:] == row[1:]
    assert "plombier" in now and "chauffagiste" in now and "climatisation" in now
    assert q("SELECT count(*) FROM company_activities WHERE last_seen_at > collected_at")[0][0] >= 1     # re-seen claims moved only last_seen_at


def test_nothing_found_and_inaccessible_are_distinct_and_retried_differently(db):
    sa.run(DSN, client=Fake({HOME: {"markdown": "Bienvenue. Contactez-nous.", "links": []}}), limit=10)
    assert q("SELECT outcome, items_found FROM activity_runs") == [("nothing_found", 0)]
    assert q("SELECT count(*) FROM company_activities")[0][0] == 0                      # no claim invented from an empty page
    # unreadable site: inaccessible, retried only after --retry-days
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM activity_runs")
    c = Fake({})
    assert sa.run(DSN, client=c, limit=10)["inaccessible"] == 1
    assert q("SELECT outcome, pages_read FROM activity_runs") == [("inaccessible", 0)]
    again = Fake(PAGES)
    assert sa.run(DSN, client=again, limit=10, refresh_days=0)["read"] == 0 and again.scrapes == []     # too soon to retry
    assert sa.run(DSN, client=again, limit=10, retry_days=0)["extracted"] == 1


def test_a_company_is_not_started_when_its_four_credits_are_not_left(db):
    c = Fake(PAGES)
    stats = sa.run(DSN, client=c, limit=10, cap=3)
    assert stats["cap_reached"] and stats["read"] == 0 and c.scrapes == [] and q("SELECT count(*) FROM activity_runs")[0][0] == 0
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO api_usage (provider, calls, credits) VALUES ('firecrawl', 7, 7)")
    stats2 = sa.run(DSN, client=c, limit=10, cap=10)                                    # 7 used + 4 needed > 10
    assert stats2["cap_reached"] and stats2["read"] == 0 and c.scrapes == []
    assert sa.run(DSN, client=c, limit=10, cap=11)["extracted"] == 1                    # exactly 4 left: runs


def test_database_constraints_and_idempotent_migration(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):                                # unknown facet
            conn.execute("INSERT INTO company_activities (company_id, facet, code, label, matched_text, passage, source_url) "
                         "VALUES (%s,'mood','x','x','x','x','u')", (cid,))
        with pytest.raises(psycopg.errors.NotNullViolation):                              # a claim without its passage is refused
            conn.execute("INSERT INTO company_activities (company_id, facet, code, label, matched_text, source_url) "
                         "VALUES (%s,'trade','plombier','x','x','u')", (cid,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO activity_runs (company_id, outcome) VALUES (%s,'no_activity')", (cid,))
        for facet in ("building_type", "commitment"):                                      # facets added by 024
            conn.execute("INSERT INTO company_activities (company_id, facet, code, label, matched_text, passage, source_url) "
                         "VALUES (%s,%s,'x','x','x','x','u')", (cid, facet))
        for name in ("017_site_activity.sql", "024_activity_facets_more.sql"):
            sql = (SQL_DIR / name).read_text(encoding="utf-8")
            conn.execute(sql)
            conn.execute(sql)                                                             # idempotent


def test_activity_kpi_counts_companies_with_claims_only(db):
    sa.run(DSN, client=Fake(PAGES), limit=10)
    _, rates = metrics.collect(DSN, target_rows=10)
    assert round(rates["match_companies_with_activity_claims"], 3) == round(1 / 3, 3)
