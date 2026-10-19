"""Press against a REAL PostgreSQL (TEST_DATABASE_URL), with a fake Firecrawl client (no network)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import press as pr  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B = "552100554", "401234562"
ART, ART2 = "https://www.leprogres.fr/economie/alpha-elec", "https://www.lyonmag.com/article/alpha-elec"


class Fake:
    def __init__(self, hits, pages=None):
        self.hits, self.pages, self.queries, self.scrapes = hits, pages or {}, [], []

    def search_hits(self, query, limit=5):
        self.queries.append(query)
        return self.hits(query) if callable(self.hits) else self.hits

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


def h(url, desc="", title="Alpha Elec inaugure"):
    return {"url": url, "title": title, "description": desc}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        for siren, name, trading in ((A, "ALPHA ELEC SARL", "Alpha Elec"), (B, "BETA SARL", None)):
            conn.execute("INSERT INTO companies (siren, legal_name, trading_name) VALUES (%s,%s,%s)", (siren, name, trading))
            conn.execute("INSERT INTO company_addresses (company_id, is_head_office, street, postal_code, city) "
                         "SELECT company_id, TRUE, '12 RUE DES LILAS', '69003', 'LYON' FROM companies WHERE siren = %s", (siren,))
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status, match_proof)
                        SELECT company_id, 'website', 'alpha.fr', 'alpha.fr', 'verified', 'SIREN on site' FROM companies WHERE siren = %s""", (A,))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def hits_for(query):
    return [h(ART, "Alpha Elec, SIREN 552 100 554, ouvre un atelier."), h("https://alpha.fr/actu", "Notre actualité"), h("https://www.societe.com/x")] \
        if "Alpha Elec" in query else []


def test_confirmed_article_keeps_url_passage_proof_and_company_without_hit_is_not_found(db):
    c = Fake(hits_for)
    s = pr.run(DSN, client=c, limit=10)
    assert s["companies"] == 2 and s["confirmed"] == 1 and s["not_found"] == 1 and s["articles_added"] == 1
    assert c.queries[0].startswith('"Alpha Elec" "LYON"') and c.scrapes == []
    row = q("SELECT article_url, publisher, mention_passage, status, evidence_type, proof FROM company_press")[0]
    assert row[0] == ART and row[1] == "leprogres.fr" and "Alpha Elec" in row[2] and row[3] == "confirmed" and row[4] == "siren_on_page" and "552 100 554" in row[5]
    # the company's own site and the directory were never stored as press
    assert q("SELECT count(*) FROM company_press WHERE article_url LIKE '%%alpha.fr%%' OR article_url LIKE '%%societe.com%%'")[0][0] == 0
    st = dict(q("SELECT c.siren, s.status FROM press_searches s JOIN companies c USING (company_id)"))
    assert st == {A: "confirmed", B: "not_found"}


def test_no_proof_means_to_verify_and_it_is_not_counted_as_press(db):
    c = Fake([h(ART, "Reportage chez Alpha Elec")], {})
    pr.run(DSN, client=c, limit=1)
    assert q("SELECT status, evidence_type, proof FROM company_press") == [("to_verify", None, None)]
    _, rates = metrics.collect(DSN, target_rows=10)
    assert rates["match_companies_with_press"] == 0 and round(rates["match_companies_with_press_to_verify"], 3) == 0.5


def test_not_searched_twice_before_refresh_and_a_later_search_upgrades_never_downgrades_or_deletes(db):
    c = Fake([h(ART, "Reportage chez Alpha Elec")])
    pr.run(DSN, client=c, limit=1)
    n = len(c.queries)
    assert pr.run(DSN, client=c, limit=1)["companies"] == 1 and len(c.queries) == n + 1     # limit 1: next company (B), A is remembered
    assert q("SELECT count(*) FROM press_searches")[0][0] == 2
    # refresh: the article is now proven by its snippet, and a second article appears
    c2 = Fake([h(ART, "Alpha Elec - SIREN 552100554"), h(ART2, "Alpha Elec a recruté")])
    s = pr.run(DSN, client=c2, limit=10, refresh_days=0)
    assert s["upgraded"] == 1 and s["articles_added"] >= 1
    assert dict(q("SELECT article_url, status FROM company_press WHERE company_id=(SELECT company_id FROM companies WHERE siren=%s)", A))[ART] == "confirmed"
    # a later search that finds nothing deletes nothing and the company status is derived from what is stored
    pr.run(DSN, client=Fake([]), limit=10, refresh_days=0)
    assert q("SELECT count(*) FROM company_press WHERE status='confirmed'")[0][0] >= 1
    assert q("SELECT s.status FROM press_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", A) == [("confirmed",)]
    # and a confirmed article is never downgraded by a snippet without proof
    pr.run(DSN, client=Fake([h(ART, "Reportage chez Alpha Elec")]), limit=10, refresh_days=0)
    assert dict(q("SELECT article_url, status FROM company_press WHERE company_id=(SELECT company_id FROM companies WHERE siren=%s)", A))[ART] == "confirmed"


def test_inaccessible_is_distinct_and_retried_after_its_own_delay(db):
    assert pr.run(DSN, client=Fake(None), limit=10)["inaccessible"] == 2
    assert q("SELECT DISTINCT status FROM press_searches") == [("inaccessible",)]
    assert q("SELECT count(*) FROM company_press")[0][0] == 0
    again = Fake([h(ART, "SIREN 552100554 Alpha Elec")])
    assert pr.run(DSN, client=again, limit=10, refresh_days=0)["companies"] == 0 and again.queries == []     # too soon to retry
    assert pr.run(DSN, client=again, limit=10, retry_days=0)["companies"] == 2


def test_a_company_is_not_started_when_its_three_credits_are_not_left(db):
    c = Fake([h(ART, "Alpha Elec")])
    assert pr.run(DSN, client=c, limit=10, cap=2)["cap_reached"] and c.queries == []
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO api_usage (provider, calls, credits) VALUES ('firecrawl', 8, 8)")
    assert pr.run(DSN, client=c, limit=10, cap=10)["companies"] == 0 and c.queries == []          # 8 used + 3 needed > 10
    assert pr.run(DSN, client=c, limit=10, cap=11)["companies"] == 1                              # exactly 3 left: one company, then 3 more needed


def test_database_constraints_and_idempotent_migration(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):                 # confirmed without proof
            conn.execute("INSERT INTO company_press (company_id, article_url, mention_passage, status) VALUES (%s,'u','p','confirmed')", (cid,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO company_press (company_id, article_url, mention_passage, status) VALUES (%s,'u','p','verified')", (cid,))
        with pytest.raises(psycopg.errors.NotNullViolation):               # no article without the line that names the company
            conn.execute("INSERT INTO company_press (company_id, article_url, status) VALUES (%s,'u','to_verify')", (cid,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO press_searches (company_id, status) VALUES (%s,'no_press')", (cid,))
        sql = (SQL_DIR / "020_press.sql").read_text(encoding="utf-8")
        conn.execute(sql)
        conn.execute(sql)


def test_press_kpi_counts_confirmed_companies_only(db):
    pr.run(DSN, client=Fake(hits_for), limit=10)
    _, rates = metrics.collect(DSN, target_rows=10)
    assert round(rates["match_companies_with_press"], 3) == 0.5 and rates["match_companies_with_press_to_verify"] == 0
