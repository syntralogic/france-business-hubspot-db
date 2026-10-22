"""Scoped bulk load and weekly delta against a REAL PostgreSQL (TEST_DATABASE_URL). Reuses the synthetic SIRENE rows of test_pipeline_pg."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.extract.sirene_bulk import ETAB_COLUMNS, UL_COLUMNS  # noqa: E402
from fbdb.load import sirene_loader, weekly  # noqa: E402
from fbdb.load.scope import Scope  # noqa: E402
from test_pipeline_pg import ET_ROWS, S1, S2, S3, S4, UL_ROWS, T1_BR, T1_HQ, T2, T3, FakeClient, flat, write_csv  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
BASE = ("000_reference_data.sql", "001_schema.sql", "002_staging.sql", "003_pipeline_state.sql", "003a_provenance.sql", "003b_financials_exercice.sql",
        "003c_address_diffusion.sql", "003d_email_priority.sql", "004_export_view.sql", "006_closure_date.sql")


@pytest.fixture()
def files(tmp_path):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in BASE:
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
    write_csv(tmp_path / "ul.csv", UL_COLUMNS, UL_ROWS)
    write_csv(tmp_path / "et.csv", ETAB_COLUMNS, ET_ROWS)
    return tmp_path


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def sirens():
    """S4 is the non-diffusible company of the synthetic rows: its company row is always created (existing behaviour), its establishment never."""
    return {r[0] for r in q("SELECT siren FROM companies")} - {S4}


def test_no_scope_loads_everything_diffusible(files):
    sirene_loader.load(files / "ul.csv", files / "et.csv", True, DSN, Scope())
    assert sirens() == {S1, S2, S3}
    assert q("SELECT notes FROM import_export_batches WHERE status='success'") == [(None,)]


def test_naf_scope_keeps_only_matching_companies_and_their_establishments(files):
    sirene_loader.load(files / "ul.csv", files / "et.csv", True, DSN, Scope.build(naf="43"))
    assert sirens() == {S3}                                                    # plumbing only: bakery and cafe are out
    assert {r[0] for r in q("SELECT siret FROM company_addresses")} == {T3}
    assert q("SELECT notes, rows_read FROM import_export_batches WHERE status='success'") == [("scope: naf=43; departments=all", 2)]


def test_department_scope_keeps_companies_with_an_establishment_there_only(files):
    sirene_loader.load(files / "ul.csv", files / "et.csv", True, DSN, Scope.build(departments="69"))
    assert sirens() == {S1}                                                    # Lyon / Villeurbanne: both establishments of S1
    assert {r[0] for r in q("SELECT siret FROM company_addresses")} == {T1_HQ, T1_BR}
    sirene_loader.load(files / "ul.csv", files / "et.csv", True, DSN, Scope.build(naf="10,56", departments="13"))
    assert sirens() == {S1, S2}                                                # a later scoped load only adds: nothing is deleted


def test_a_company_with_no_establishment_in_the_departments_is_not_loaded(files):
    sirene_loader.load(files / "ul.csv", files / "et.csv", True, DSN, Scope.build(departments="75"))
    assert sirens() == set() and q("SELECT count(*) FROM company_addresses")[0][0] == 0


def test_weekly_delta_ignores_new_out_of_scope_companies_but_keeps_following_known_ones(files, monkeypatch):
    sirene_loader.load(files / "ul.csv", files / "et.csv", True, DSN, Scope.build(naf="43"))
    assert sirens() == {S3}
    monkeypatch.setenv("SCOPE_NAF", "43")
    delta = [flat(UL_ROWS[0], ET_ROWS[0]),                                      # S1 bakery: out of scope and unknown: ignored
             flat(UL_ROWS[2], {**ET_ROWS[3], "etatAdministratifEtablissement": "F", "dateFermetureEtablissement": "2026-10-10"})]   # S3 closes: still seen
    weekly.run("2026-10-01T00:00:00", DSN, client=FakeClient(delta))
    assert sirens() == {S3}
    assert q("SELECT status FROM company_addresses WHERE siret=%s", T3) == [("closed",)]
