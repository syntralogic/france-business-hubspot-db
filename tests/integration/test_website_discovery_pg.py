"""Website discovery against a REAL PostgreSQL (TEST_DATABASE_URL), with a fake Firecrawl client (no network)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import website_discovery as wd  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B = "552100554", "401234562"
A_TXT = "SIREN 552 100 554"


class Fake:
    def __init__(self, results, pages):
        self.results, self.pages, self.searches = results, pages, 0

    def search(self, query, limit=5):
        self.searches += 1
        return self.results

    def scrape(self, url):
        return self.pages.get(url)


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        for siren, name in ((A, "ALPHA SARL"), (B, "BETA SARL")):
            conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s,%s)", (siren, name))
            conn.execute("INSERT INTO company_addresses (company_id, is_head_office, city) "
                         "SELECT company_id, TRUE, 'LYON' FROM companies WHERE siren = %s", (siren,))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def test_proven_site_is_stored_verified_and_not_searched_again(db):
    client = Fake(["https://alpha.fr/x"], {
        "https://alpha.fr": {"markdown": "Accueil", "links": ["/mentions-legales", "https://facebook.com/alpha"]},
        "https://alpha.fr/mentions-legales": {"markdown": A_TXT, "links": []},
    })
    s = wd.run(DSN, client=client, limit=10)
    assert s["proven"] == 1 and s["not_proven"] == 1 and s["socials_added"] == 1   # BETA: candidates found, SIREN absent
    assert q("SELECT value_normalized, value_status, match_url FROM company_contacts WHERE contact_type='website'") == [
        ("alpha.fr", "verified", "https://alpha.fr/mentions-legales")]
    assert q("SELECT s.outcome FROM website_searches s JOIN companies c USING (company_id) ORDER BY c.siren DESC") == [
        ("proven",), ("not_proven",)]
    # second run: both already searched, ALPHA already has a website -> nothing to do and no credit spent
    before = client.searches
    assert wd.run(DSN, client=client, limit=10)["read"] == 0 and client.searches == before


def test_not_proven_stores_no_website(db):
    s = wd.run(DSN, client=Fake(["https://www.societe.com/x"], {}), limit=10)
    assert s["no_results"] == 2 and q("SELECT count(*) FROM company_contacts")[0][0] == 0
