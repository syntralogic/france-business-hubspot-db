"""Site signals against a REAL PostgreSQL (TEST_DATABASE_URL), with a fake Firecrawl client (no network)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import site_signals as ss  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B, C = "552100554", "401234562", "552120222"
PROOF = "SIREN 552 100 554 on https://alpha.fr/mentions-legales"
HOME = "https://alpha.fr"


class Fake:
    def __init__(self, pages):
        self.pages, self.scrapes = pages, []

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


PAGES = {
    HOME: {"markdown": "# Alpha Elec\nNous recrutons un électricien en CDI.\n![Chantier Lyon 2021 pour un client](c.jpg)",
           "links": ["/realisations", "/partenaires", "/blog/chantier", "/mentions-legales"]},
    HOME + "/realisations": {"markdown": "Installation de 24 panneaux solaires pour la mairie de Villeurbanne (2022).", "links": []},
    HOME + "/partenaires": {"markdown": "## Nos partenaires\n- Daikin\n- Legrand\n", "links": []},
}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        for siren, name in ((A, "ALPHA ELEC"), (B, "BETA SARL"), (C, "GAMMA SAS")):
            conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s,%s)", (siren, name))
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status, match_proof, is_primary)
                        SELECT company_id, 'website', 'alpha.fr', 'alpha.fr', 'verified', %s, TRUE FROM companies WHERE siren = %s""", (PROOF, A))
        conn.execute("""INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status)
                        SELECT company_id, 'website', 'beta.fr', 'beta.fr', 'sourced' FROM companies WHERE siren = %s""", (B,))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def test_only_a_proven_site_is_read_and_every_signal_keeps_passage_url_proof_and_status(db):
    c = Fake(PAGES)
    stats = ss.run(DSN, client=c, limit=10)
    assert stats["read"] == 1 and stats["extracted"] == 1 and c.scrapes == [HOME, HOME + "/realisations", HOME + "/partenaires"]
    rows = {(r[0], r[1]): r[2:] for r in q("SELECT kind, code, name, relation, year_mentioned, passage, source_url, site_proof, status FROM company_site_signals")}
    hiring = rows[("hiring", "role:electricien")]
    assert hiring[0] == "Electricien" and hiring[1] == "cdi" and hiring[4] == HOME and hiring[5] == PROOF and hiring[6] == "claimed"
    real = [v for k, v in rows.items() if k[0] == "realisation"]
    assert len(real) == 1 and real[0][2] == 2022 and real[0][4] == HOME + "/realisations" and "mairie de Villeurbanne" in real[0][3]
    assert rows[("partner", "n:daikin")][4] == HOME + "/partenaires" and rows[("partner", "n:daikin")][3] == "Nos partenaires > Daikin"
    # the photo with a year and a client in its alt text is NOT a realisation
    assert q("SELECT count(*) FROM company_site_signals WHERE passage ILIKE '%%Chantier Lyon%%'")[0][0] == 0
    assert q("SELECT outcome, pages_read, items_found FROM signal_runs") == [("extracted", 3, 4)]
    # a partner is never matched to a company of our base
    assert q("SELECT count(*) FROM company_site_signals WHERE company_id NOT IN (SELECT company_id FROM companies WHERE siren=%s)", A)[0][0] == 0


def test_not_read_twice_before_the_refresh_delay_and_nothing_is_deleted_on_refresh(db):
    c = Fake(PAGES)
    ss.run(DSN, client=c, limit=10)
    n = len(c.scrapes)
    assert ss.run(DSN, client=c, limit=10)["read"] == 0 and len(c.scrapes) == n
    first = q("SELECT kind, code, passage, source_url, collected_at FROM company_site_signals ORDER BY kind, code")
    first_hiring = next(r for r in first if r[0] == "hiring")
    # the site no longer recruits and drops a partner, adds another
    changed = Fake({HOME: {"markdown": "Bienvenue.", "links": ["/partenaires"]}, HOME + "/partenaires": {"markdown": "Nos partenaires : Atlantic.", "links": []}})
    s = ss.run(DSN, client=changed, limit=10, refresh_days=0)
    assert s["read"] == 1 and s["signals_added"] == 1
    now = {(r[0], r[1]): r for r in q("SELECT kind, code, passage, source_url, collected_at FROM company_site_signals")}
    for row in first:
        assert now[(row[0], row[1])][2:] == row[2:]               # nothing deleted, first passage / URL kept
    assert ("partner", "n:atlantic") in now
    # the recruitment claim no longer seen is kept as it was (last_seen_at untouched): "seen recruiting on <date>", not "recruiting now"
    assert q("SELECT last_seen_at = collected_at FROM company_site_signals WHERE kind='hiring'") == [(True,)]
    # a claim seen again moves only last_seen_at
    ss.run(DSN, client=Fake(PAGES), limit=10, refresh_days=0)
    assert q("SELECT last_seen_at > collected_at FROM company_site_signals WHERE kind='hiring'") == [(True,)]
    assert [r[2:] for r in q("SELECT kind, code, passage, source_url FROM company_site_signals WHERE kind='hiring'")] == [(first_hiring[2], first_hiring[3])]


def test_nothing_found_and_inaccessible_are_distinct_and_retried_differently(db):
    ss.run(DSN, client=Fake({HOME: {"markdown": "Bienvenue. Contactez-nous.", "links": []}}), limit=10)
    assert q("SELECT outcome, items_found FROM signal_runs") == [("nothing_found", 0)]
    assert q("SELECT count(*) FROM company_site_signals")[0][0] == 0
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM signal_runs")
    assert ss.run(DSN, client=Fake({}), limit=10)["inaccessible"] == 1
    assert q("SELECT outcome, pages_read FROM signal_runs") == [("inaccessible", 0)]
    again = Fake(PAGES)
    assert ss.run(DSN, client=again, limit=10, refresh_days=0)["read"] == 0 and again.scrapes == []
    assert ss.run(DSN, client=again, limit=10, retry_days=0)["extracted"] == 1


def test_a_company_is_not_started_when_its_four_credits_are_not_left(db):
    c = Fake(PAGES)
    stats = ss.run(DSN, client=c, limit=10, cap=3)
    assert stats["cap_reached"] and stats["read"] == 0 and c.scrapes == [] and q("SELECT count(*) FROM signal_runs")[0][0] == 0
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO api_usage (provider, calls, credits) VALUES ('firecrawl', 7, 7)")
    assert ss.run(DSN, client=c, limit=10, cap=10)["read"] == 0 and c.scrapes == []
    assert ss.run(DSN, client=c, limit=10, cap=11)["extracted"] == 1


def test_database_constraints_and_idempotent_migration(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):                      # unknown kind
            conn.execute("INSERT INTO company_site_signals (company_id, kind, code, matched_text, passage, source_url) VALUES (%s,'mood','x','x','x','u')", (cid,))
        with pytest.raises(psycopg.errors.NotNullViolation):                    # no signal without its passage
            conn.execute("INSERT INTO company_site_signals (company_id, kind, code, matched_text, source_url) VALUES (%s,'partner','n:x','x','u')", (cid,))
        with pytest.raises(psycopg.errors.CheckViolation):                      # a site claim is never "verified"
            conn.execute("INSERT INTO company_site_signals (company_id, kind, code, matched_text, passage, source_url, status) "
                         "VALUES (%s,'partner','n:x','x','x','u','verified')", (cid,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO signal_runs (company_id, outcome) VALUES (%s,'no_partner')", (cid,))
        sql = (SQL_DIR / "019_site_signals.sql").read_text(encoding="utf-8")
        conn.execute(sql)
        conn.execute(sql)


def test_site_signals_kpi_counts_companies_with_a_signal_only(db):
    ss.run(DSN, client=Fake(PAGES), limit=10)
    _, rates = metrics.collect(DSN, target_rows=10)
    assert round(rates["match_companies_with_site_signals"], 3) == round(1 / 3, 3)
