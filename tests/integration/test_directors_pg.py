"""Directors enrichment against a REAL PostgreSQL (same TEST_DATABASE_URL as test_pipeline_pg.py)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import directors  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

A, B, C, D, E = "552100554", "401234563", "777888997", "111222335", "222333449"


def person(last, first, role, year="1970"):
    return {"nom": last, "prenoms": first, "qualite": role, "annee_de_naissance": year, "type_dirigeant": "personne physique"}


class FakeAnnuaire:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get_company(self, siren):
        self.calls.append(siren)
        v = self.data.get(siren)
        if isinstance(v, Exception):
            raise v
        return None if v is None else {"siren": siren, "dirigeants": v}


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in ("001_schema.sql", "002_staging.sql", "003_pipeline_state.sql", "004_export_view.sql",
                  "005_directors_enrichment.sql"):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        rows = [  # siren, name, sole proprietor, diffusible, status
            (A, "ALPHA SARL", False, True, "active"),
            (B, "BETA EI", True, True, "active"),          # sole proprietor: out of scope
            (C, "CHARLIE SAS", False, True, "active"),     # API returns no director
            (D, "DELTA NON DIFFUSIBLE", False, False, "active"),
            (E, "ECHO FERMEE", False, True, "closed"),     # closed: out of scope
        ]
        for s, n, sole, diff, st in rows:
            conn.execute(
                "INSERT INTO companies (siren, legal_name, is_sole_proprietor, is_diffusible, status) "
                "VALUES (%s,%s,%s,%s,%s)", (s, n, sole, diff, st))
    yield


def q(sql, *args):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, args).fetchall()


def test_first_run_scope_history_and_idempotency(db):
    client = FakeAnnuaire({
        A: [person("DUPONT", "Jean", "Gérant"), person("MARTIN", "Anne", "Président"),
            {"siren": "377652938", "denomination": "AUDITEX", "type_dirigeant": "personne morale"}],
        C: [],
    })
    st = directors.run(DSN, client=client)
    assert sorted(client.calls) == sorted([A, C])          # sole proprietor, non-diffusible, closed never queried
    assert st["added"] == 2 and st["updated"] == 1 and st["unchanged"] == 1 and st["errors"] == 0
    assert q("SELECT count(*) FROM company_directors")[0][0] == 2
    hist = q("SELECT field_name, old_value, new_value, change_type FROM company_change_history "
             "WHERE table_name='company_directors' ORDER BY new_value")
    assert hist == [("director", None, "DUPONT Jean (Gérant)", "insert"), ("director", None, "MARTIN Anne (Président)", "insert")]
    assert q("SELECT status, rows_added FROM import_export_batches WHERE batch_type='enrich'") == [("success", 2)]
    assert q("SELECT count(*) FROM companies WHERE directors_checked_at IS NOT NULL")[0][0] == 2

    # Idempotent: a second run inside the refresh window queries nobody and writes no history.
    client2 = FakeAnnuaire({})
    st2 = directors.run(DSN, client=client2)
    assert client2.calls == [] and st2["read"] == 0
    assert q("SELECT count(*) FROM company_change_history WHERE table_name='company_directors'")[0][0] == 2


def test_director_change_is_recorded_old_and_new(db):
    directors.run(DSN, client=FakeAnnuaire({A: [person("DUPONT", "Jean", "Gérant"), person("MARTIN", "Anne", "Président")], C: []}))
    # Weekly refresh: Martin left, Leroy arrived, Dupont unchanged.
    client = FakeAnnuaire({A: [person("DUPONT", "Jean", "Gérant"), person("LEROY", "Paul", "Directeur général")], C: []})
    st = directors.run(DSN, client=client, refresh_days=0)
    assert st["added"] == 1 and st["removed"] == 1 and st["updated"] == 1 and st["unchanged"] == 1
    active = q("SELECT last_name FROM company_directors WHERE is_active ORDER BY last_name")
    assert active == [("DUPONT",), ("LEROY",)]
    assert q("SELECT end_date IS NOT NULL FROM company_directors WHERE last_name='MARTIN'") == [(True,)]
    hist = q("SELECT old_value, new_value, change_type FROM company_change_history "
             "WHERE table_name='company_directors' AND batch_id = (SELECT max(batch_id) FROM import_export_batches) "
             "ORDER BY change_type")
    assert hist == [("MARTIN Anne (Président)", None, "delete"), (None, "LEROY Paul (Directeur général)", "insert")]


def test_returning_director_is_reactivated_without_unique_violation(db):
    directors.run(DSN, client=FakeAnnuaire({A: [person("DUPONT", "Jean", "Gérant")], C: []}))
    directors.run(DSN, client=FakeAnnuaire({A: [], C: []}), refresh_days=0)
    assert q("SELECT is_active FROM company_directors") == [(False,)]
    directors.run(DSN, client=FakeAnnuaire({A: [person("DUPONT", "Jean", "Gérant")], C: []}), refresh_days=0)
    assert q("SELECT is_active, end_date FROM company_directors") == [(True, None)]
    assert q("SELECT count(*) FROM company_directors")[0][0] == 1


def test_api_failure_marks_batch_partial_and_company_is_retried(db):
    client = FakeAnnuaire({A: RuntimeError("HTTP 503"), C: [person("MARTIN", "Anne", "Présidente")]})
    st = directors.run(DSN, client=client)
    assert st["errors"] == 1 and st["added"] == 1
    assert q("SELECT status FROM import_export_batches WHERE batch_type='enrich'") == [("partial",)]
    # A failed company keeps directors_checked_at NULL, so the next run picks it up first.
    assert q("SELECT siren FROM companies WHERE directors_checked_at IS NULL AND is_diffusible AND status='active' "
             "AND NOT is_sole_proprietor") == [(A,)]
