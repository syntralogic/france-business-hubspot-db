"""Pappers loader + daily cap against a REAL PostgreSQL (TEST_DATABASE_URL), API stubbed."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.common import usage  # noqa: E402
from fbdb.enrich import pappers  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class Fake:
    def __init__(self):
        self.calls = []

    def get_company(self, siren):
        self.calls.append(siren)
        if siren == "732829320":
            return None
        if siren == "404833048":
            return {"finances": []}
        return {"finances": [{"annee": 2023, "chiffre_affaires": 500000, "resultat": 1000, "effectif": 4}]}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("""INSERT INTO companies (siren, legal_name, is_diffusible, status, is_sole_proprietor) VALUES
          ('552100554','ALPHA',TRUE,'active',FALSE),('732829320','BETA',TRUE,'active',FALSE),
          ('404833048','GAMMA',TRUE,'active',FALSE),('123456782','SOLO',TRUE,'active',TRUE),('111111118','HIDDEN',FALSE,'active',FALSE)""")


def test_reserve_is_capped_and_release_gives_back(db):
    with psycopg.connect(DSN) as c:
        assert [usage.reserve(c, "x", 3, 2), usage.reserve(c, "x", 3, 2)] == [True, False]
        assert usage.used_today(c, "x") == (1, 2)
        usage.release(c, "x", 2)
        assert usage.used_today(c, "x") == (0, 0)
        assert usage.reserve(c, "x", 1, 2) is False        # a single call costlier than the cap never runs


def test_run_scope_cap_and_idempotence(db):
    fake = Fake()
    s = pappers.run(DSN, fake, limit=10, cap=2)
    assert s["read"] == 2 and s["cap_reached"] and s["credits_used"] == 2
    s2 = pappers.run(DSN, fake, limit=10, cap=10)           # same day: 2 already used, 8 left; 3rd company only
    assert s2["read"] == 1 and not s2["cap_reached"]
    assert sorted(fake.calls) == ["404833048", "552100554", "732829320"]   # sole proprietor and non-diffusible never queried
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT revenue_eur, employees FROM company_financials").fetchall() == [(500000, 4)]
        assert usage.used_today(c, "pappers") == (3, 3)
    s3 = pappers.run(DSN, fake)                             # all checked within 180 days: nothing to do
    assert s3["read"] == 0


class Scripted:
    def __init__(self, payloads):
        self.payloads = payloads

    def get_company(self, siren):
        v = self.payloads[siren]
        if isinstance(v, Exception):
            raise v
        return v


def _fin(**over):
    base = {"annee": 2023, "date_cloture_exercice": "2023-12-31", "duree_exercice": 12, "chiffre_affaires": 500000,
            "resultat": 1000, "effectif": 4}
    base.update(over)
    return {"finances": [base]}


def test_exercise_provenance_and_two_exercises_same_year(db):
    two = {"finances": [_fin()["finances"][0], _fin(date_cloture_exercice="2023-03-31", duree_exercice=3, chiffre_affaires=90000)["finances"][0]]}
    pappers.run(DSN, Scripted({"552100554": two, "732829320": None, "404833048": {"finances": []}}), limit=1)
    with psycopg.connect(DSN) as c:
        rows = c.execute("SELECT fiscal_year, period_end::text, duration_months, revenue_eur, source_url FROM company_financials "
                         "ORDER BY period_end").fetchall()
    assert [r[:4] for r in rows] == [(2023, "2023-03-31", 3, 90000), (2023, "2023-12-31", 12, 500000)]
    assert all(r[4].endswith("?siren=552100554") and "token" not in r[4] for r in rows)


def test_changed_figure_goes_to_conflicts_first_value_kept_missing_value_filled(db):
    s1 = Scripted({"552100554": _fin(effectif=None), "732829320": None, "404833048": None})
    pappers.run(DSN, s1, limit=1)
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE companies SET financials_checked_at = NULL")
        c.commit()
    s2 = Scripted({"552100554": _fin(chiffre_affaires=777000, effectif=6), "732829320": None, "404833048": None})
    st = pappers.run(DSN, s2, limit=1)
    assert st["conflicts"] == 1
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT revenue_eur, employees FROM company_financials").fetchall() == [(500000, 6)]   # kept; 6 filled in
        assert c.execute("SELECT field_name, kept_value, other_value FROM field_conflicts").fetchall() == [
            ("financials:2023-12-31:revenue_eur", "500000.00", "777000.00")]
        assert c.execute("SELECT count(*) FROM company_financials").fetchone()[0] == 1


def test_status_found_not_found_inaccessible_and_not_searched(db):
    fake = Scripted({"552100554": _fin(), "732829320": None, "404833048": {"finances": []}})
    pappers.run(DSN, fake, limit=10)
    err = Scripted({"552100554": RuntimeError("boom"), "732829320": RuntimeError("boom"), "404833048": RuntimeError("boom")})
    with psycopg.connect(DSN) as c:
        st = dict(c.execute("SELECT co.siren, f.status FROM financials_checks f JOIN companies co USING (company_id)").fetchall())
        assert st == {"552100554": "found", "732829320": "not_found", "404833048": "not_found"}
        det = dict(c.execute("SELECT co.siren, f.detail FROM financials_checks f JOIN companies co USING (company_id)").fetchall())
        assert "unknown" in det["732829320"] and "not proof" in det["404833048"]
        assert c.execute("SELECT count(*) FROM financials_checks f JOIN companies co USING (company_id) WHERE co.siren IN ('123456782','111111118')").fetchone()[0] == 0   # never searched
        c.execute("UPDATE companies SET financials_checked_at = NULL")
        c.commit()
    pappers.run(DSN, err, limit=10)
    with psycopg.connect(DSN) as c:
        assert {r[0] for r in c.execute("SELECT status FROM financials_checks").fetchall()} == {"inaccessible"}
        assert c.execute("SELECT count(*) FROM companies WHERE financials_checked_at IS NULL AND siren IN ('552100554','732829320','404833048')").fetchone()[0] == 3   # retried next run
        assert c.execute("SELECT count(*) FROM company_financials").fetchone()[0] == 1                # the earlier data is not erased


def test_hubspot_view_never_uses_a_short_exercise_as_annual_revenue(db):
    with psycopg.connect(DSN, autocommit=True) as c:
        cid = c.execute("SELECT company_id FROM companies WHERE siren='552100554'").fetchone()[0]
        c.execute("INSERT INTO company_addresses (company_id, siret, nic, is_head_office, city) VALUES (%s,'55210055400013','00013',TRUE,'LYON')", (cid,))
        sid = c.execute("SELECT source_id FROM data_sources WHERE code='pappers'").fetchone()[0]
        for year, end, dur, rev in ((2022, "2022-12-31", 12, 400000), (2023, "2023-12-31", 6, 150000)):
            c.execute("INSERT INTO company_financials (company_id, fiscal_year, period_end, duration_months, revenue_eur, source_id) VALUES (%s,%s,%s,%s,%s,%s)",
                      (cid, year, end, dur, rev, sid))
        row = c.execute("SELECT revenue, revenue_year FROM v_hubspot_companies").fetchone()
    assert (float(row[0]), row[1]) == (400000.0, 2022)


def test_003b_is_idempotent_on_a_database_that_already_has_rows(db):
    pappers.run(DSN, Fake(), limit=1)
    with psycopg.connect(DSN, autocommit=True) as c:
        for _ in range(2):
            c.execute((SQL_DIR / "003b_financials_exercice.sql").read_text(encoding="utf-8"))
        assert c.execute("SELECT count(*) FROM company_financials").fetchone()[0] == 1
