"""Social accounts by search against a REAL PostgreSQL (TEST_DATABASE_URL), with a fake Firecrawl client (no network)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import social_search as ss  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B = "552100554", "401234562"
A_SNIPPET = "SIREN 552 100 554"
FB_A, IG_A, LI_A = "https://www.facebook.com/alphaelec", "https://www.instagram.com/alphaelec", "https://www.linkedin.com/in/eva-durand"


class Fake:
    """search_hits answers by site operator found in the query; a missing network answers []. scrape returns the configured pages."""

    def __init__(self, by_site, pages=None):
        self.by_site, self.pages, self.queries, self.scrapes = by_site, pages or {}, [], []

    def search_hits(self, query, limit=5):
        self.queries.append(query)
        for site, hits in self.by_site.items():
            if f"site:{site} " in query:
                return hits
        return []

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


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
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status)
                        SELECT company_id, 'phone', '04 72 00 00 00', '+33472000000', 'sourced' FROM companies WHERE siren = %s""", (A,))
        conn.execute("""INSERT INTO company_interlocutors (company_id, first_name, last_name, function_category)
                        SELECT company_id, 'Eva', 'Durand', 'director' FROM companies WHERE siren = %s""", (A,))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def hit(url, title="", description=""):
    return {"url": url, "title": title, "description": description}


def standard_client():
    return Fake({
        "facebook.com": [hit(FB_A + "/about", "Alpha Elec", "Electricien. Tel 04 72 00 00 00")],       # proof: our phone, in the snippet
        "instagram.com": [hit(IG_A, "Alpha Elec (@alphaelec)", "Photos de chantier")],                  # no proof
        "linkedin.com/in": [hit("https://fr.linkedin.com/in/eva-durand", "Eva Durand - Gerante - Alpha Elec", A_SNIPPET)],
    })


def test_full_run_confirmed_to_verify_not_found_and_inaccessible(db):
    c = standard_client()
    real = c.search_hits

    def refuse_linkedin_company(query, limit=5):         # the provider refuses this query shape
        if "site:linkedin.com/company" in query:
            c.queries.append(query)
            return None
        return real(query, limit)

    c.search_hits = refuse_linkedin_company
    stats = ss.run(DSN, client=c, limit=10)
    assert stats["companies"] == 2 and stats["errors"] == 0
    # A: facebook confirmed (phone), instagram to_verify, tiktok/youtube not_found, linkedin_company inaccessible, director confirmed
    rows = dict(((r[0], r[1]), r[2:]) for r in q(
        """SELECT c.siren, s.network, s.status, s.candidate_url, s.evidence_type FROM social_searches s
           JOIN companies c USING (company_id) ORDER BY 1, 2"""))
    assert rows[(A, "facebook")] == ("confirmed", FB_A, "phone_match")
    assert rows[(A, "instagram")] == ("to_verify", IG_A, None)
    assert rows[(A, "tiktok")] == ("not_found", None, None) and rows[(A, "youtube")] == ("not_found", None, None)
    assert rows[(A, "linkedin_company")] == ("inaccessible", None, None)
    assert rows[(A, "linkedin_director")] == ("confirmed", LI_A, "siren_on_page")
    # B receives the SAME hits from the fake, but none of them proves anything for B (our phone / SIREN are A's): candidates only
    assert {k[1]: v[0] for k, v in rows.items() if k[0] == B} == {
        "facebook": "to_verify", "instagram": "to_verify", "tiktok": "not_found", "linkedin_company": "inaccessible", "youtube": "not_found"}
    assert not any(k == (B, "linkedin_director") for k in rows)          # B has no director: nothing to search
    # only the PROVEN account became a contact, with its proof and the right source; the candidate did not
    assert q("""SELECT k.contact_type, k.platform, k.value, k.value_status, k.match_proof, d.code FROM company_contacts k
                JOIN data_sources d USING (source_id) WHERE k.contact_type IN ('social','linkedin')""") == [
        ("social", "facebook", FB_A, "sourced", "account found by web search, phone_match: Electricien. Tel 04 72 00 00 00", "social_search")]
    # the director's LinkedIn goes on the person, with the proof
    (url, proof), = q("SELECT profile_url, match_proof FROM company_interlocutors")
    assert url == LI_A and "siren_on_page" in proof
    # memory + per-network status + date
    assert q("SELECT count(*) FROM social_searches WHERE last_verified_at IS NULL")[0][0] == 0
    # KPIs keep confirmed accounts (A) and unproven candidates (B only has candidates) apart
    _, rates = metrics.collect(DSN, target_rows=10)
    assert rates["match_companies_with_social_account"] == 0.5 and rates["match_companies_with_social_to_verify"] == 0.5


def test_second_run_pays_nothing_again_except_inaccessible_after_the_retry_delay(db):
    c = standard_client()
    ss.run(DSN, client=c, limit=10)
    before = len(c.queries)
    again = ss.run(DSN, client=c, limit=10)
    assert again["companies"] == 0 and len(c.queries) == before                 # nothing due: facebook covered, the rest remembered
    # inaccessible rows are retried after --retry-days
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE social_searches SET status='inaccessible', candidate_url=NULL, evidence_type=NULL, proof=NULL, proof_url=NULL "
                     "WHERE network='tiktok'")
    retry = ss.run(DSN, client=c, limit=10, retry_days=0)
    assert retry["searches"] == q("SELECT count(*) FROM social_searches WHERE network='tiktok'")[0][0] and len(c.queries) > before


def test_a_network_already_linked_from_the_site_is_not_searched(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, platform, value, value_normalized, value_status, match_proof)
                        SELECT company_id, 'social', 'facebook', 'https://facebook.com/b', 'https://facebook.com/b', 'sourced', 'link on proven site'
                        FROM companies WHERE siren = %s""", (B,))
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status, match_proof)
                        SELECT company_id, 'linkedin', 'https://www.linkedin.com/company/beta', 'https://www.linkedin.com/company/beta',
                               'sourced', 'link on proven site' FROM companies WHERE siren = %s""", (B,))
    c = standard_client()
    ss.run(DSN, client=c, limit=10, networks=("facebook", "linkedin_company"))
    assert q("SELECT network FROM social_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", B) == []   # nothing searched for B
    assert q("SELECT count(*) FROM social_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", A)[0][0] == 2


def test_networks_option_restricts_the_paid_calls_and_rejects_unknown_ones(db):
    c = standard_client()
    ss.run(DSN, client=c, limit=10, networks=("youtube",))
    assert len(c.queries) == 2 and all("site:youtube.com" in x for x in c.queries)
    with pytest.raises(ValueError):
        ss.run(DSN, client=c, networks=("myspace",))


def test_limit_counts_companies_not_searches(db):
    c = standard_client()
    stats = ss.run(DSN, client=c, limit=1)
    assert stats["companies"] == 1 and q("SELECT count(DISTINCT company_id) FROM social_searches")[0][0] == 1


def test_daily_cap_stops_the_run_and_records_nothing_for_the_search_that_did_not_run(db):
    c = standard_client()
    stats = ss.run(DSN, client=c, limit=10, networks=("instagram",), cap=2)          # search (1) + scrape (1), then nothing left
    assert stats["cap_reached"] and stats["searches"] == 1
    assert q("SELECT s.status FROM social_searches s JOIN companies c USING (company_id) ORDER BY c.siren") == [("to_verify",)]
    # the company that was not reached is simply not recorded: it will be searched tomorrow
    assert q("SELECT count(*) FROM social_searches")[0][0] == 1


def test_a_later_search_never_silently_replaces_or_erases_a_candidate(db):
    ss.run(DSN, client=standard_client(), limit=10, networks=("instagram",))
    assert q("SELECT s.candidate_url FROM social_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", A) == [(IG_A,)]
    # refresh with a different candidate and no proof: the first one is kept, the other is a recorded conflict
    other = "https://www.instagram.com/alpha_elec_lyon"
    ss.run(DSN, client=Fake({"instagram.com": [hit(other, "Alpha", "")]}), limit=10, networks=("instagram",), refresh_days=0)
    assert q("SELECT s.candidate_url, s.status FROM social_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", A) == [(IG_A, "to_verify")]
    assert q("SELECT f.field_name, f.kept_value, f.other_value FROM field_conflicts f JOIN companies c USING (company_id) WHERE c.siren=%s",
             A) == [("social:instagram", IG_A, other)]
    # a refresh that finds nothing does not erase the candidate either: only the date moves
    ss.run(DSN, client=Fake({}), limit=10, networks=("instagram",), refresh_days=0)
    assert q("SELECT s.candidate_url, s.status FROM social_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", A) == [(IG_A, "to_verify")]
    # a PROVEN account replaces the candidate (and the displaced URL is recorded)
    ss.run(DSN, client=Fake({"instagram.com": [hit(other, "Alpha", "Tel 04 72 00 00 00")]}), limit=10, networks=("instagram",), refresh_days=0)
    assert q("SELECT s.candidate_url, s.status, s.evidence_type FROM social_searches s JOIN companies c USING (company_id) WHERE c.siren=%s", A) == [
        (other, "confirmed", "phone_match")]
    assert q("SELECT value FROM company_contacts WHERE platform='instagram'") == [(other,)]
    assert sorted(r[0] for r in q("SELECT f.other_value FROM field_conflicts f JOIN companies c USING (company_id) WHERE c.siren=%s", A)) \
        == sorted([other, IG_A])


def test_unexpected_provider_error_records_nothing_and_the_run_goes_on(db):
    class Boom(Fake):
        def search_hits(self, query, limit=5):
            if "facebook" in query:
                raise RuntimeError("boom")
            return super().search_hits(query, limit)

    stats = ss.run(DSN, client=Boom({}), limit=10, networks=("facebook", "instagram"))
    assert stats["errors"] == 2 and stats["searches"] == 2
    assert q("SELECT DISTINCT network FROM social_searches") == [("instagram",)]


def test_database_refuses_a_confirmation_without_proof_and_a_candidate_on_a_not_found_row(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        for status, url, ev, proof in (("confirmed", FB_A, None, None), ("confirmed", FB_A, "phone_match", None),
                                       ("to_verify", None, None, None), ("not_found", FB_A, None, None)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO social_searches (company_id, network, status, candidate_url, evidence_type, proof) "
                             "VALUES (%s,'facebook',%s,%s,%s,%s)", (cid, status, url, ev, proof))
        with pytest.raises(psycopg.errors.CheckViolation):          # the director's network needs a person, the others must not have one
            conn.execute("INSERT INTO social_searches (company_id, network, status) VALUES (%s,'linkedin_director','not_found')", (cid,))


def test_migration_016_is_idempotent(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        sql = (SQL_DIR / "016_social_search.sql").read_text(encoding="utf-8")
        conn.execute(sql)
        conn.execute(sql)
    assert q("SELECT count(*) FROM data_sources WHERE code='social_search'")[0][0] == 1
