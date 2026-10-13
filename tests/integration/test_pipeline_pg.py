"""End-to-end test against a REAL PostgreSQL: schema -> bulk load -> weekly delta -> HubSpot export.

Skipped unless TEST_DATABASE_URL is set (an empty throw-away database, e.g. createdb fbdb_test).
    TEST_DATABASE_URL=postgresql://user:pw@localhost/fbdb_test pytest tests/integration -q
"""
import csv
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402

from fbdb.export.hubspot import export_companies  # noqa: E402
from fbdb.extract.sirene_bulk import ETAB_COLUMNS, UL_COLUMNS  # noqa: E402
from fbdb.load import sirene_loader, weekly  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _luhn_digit(base: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def siren(n: int) -> str:
    b = f"{n:08d}"
    return b + _luhn_digit(b)


def siret(sr: str, nic: int) -> str:
    b = sr + f"{nic:04d}"
    return b + _luhn_digit(b)


S1, S2, S3, S4 = siren(55210055), siren(40123456), siren(77788899), siren(11122233)
T1_HQ, T1_BR = siret(S1, 1), siret(S1, 2)
T2, T3, T4 = siret(S2, 1), siret(S3, 1), siret(S4, 1)


def ul(s, name, naf="10.71C", diff="O", state="A", processed="2026-10-01T08:00:00", cj="5499"):
    r = {c: "" for c in UL_COLUMNS}
    r.update(siren=s, statutDiffusionUniteLegale=diff, denominationUniteLegale=name, categorieJuridiqueUniteLegale=cj,
             etatAdministratifUniteLegale=state, dateCreationUniteLegale="2012-03-01", activitePrincipaleUniteLegale=naf,
             dateDernierTraitementUniteLegale=processed)
    return r


def et(s, t, nic, hq, street, postal, city, state="A", naf="10.71C", diff="O", rm="", processed="2026-10-01T08:00:00"):
    r = {c: "" for c in ETAB_COLUMNS}
    r.update(siren=s, nic=f"{nic:05d}", siret=t, statutDiffusionEtablissement=diff, etablissementSiege=hq,
             etatAdministratifEtablissement=state, numeroVoieEtablissement="12", typeVoieEtablissement="RUE",
             libelleVoieEtablissement=street, codePostalEtablissement=postal, libelleCommuneEtablissement=city,
             activitePrincipaleEtablissement=naf, activitePrincipaleRegistreMetiersEtablissement=rm,
             dateDernierTraitementEtablissement=processed, dateCreationEtablissement="2012-03-01")
    return r


UL_ROWS = [
    ul(S1, "BOULANGERIE DUPONT"),
    ul(S2, "CAFE DE L'ETOILE MULLER", naf="56.30Z"),
    ul(S3, "PLOMBERIE SAINT-ETIENNE", naf="43.22A"),
    ul(S4, "SOCIETE NON DIFFUSIBLE", diff="N"),
]
ET_ROWS = [
    et(S1, T1_HQ, 1, "true", "DE LA PAIX", "69003", "LYON 3EME", rm="1071C"),
    et(S1, T1_BR, 2, "false", "DES ECOLES", "69100", "VILLEURBANNE"),
    et(S2, T2, 1, "true", "DE LA REPUBLIQUE", "13001", "MARSEILLE 1ER", naf="56.30Z"),
    et(S3, T3, 1, "true", "JEAN JAURES", "42000", "SAINT-ETIENNE", naf="43.22A"),
    et(S4, T4, 1, "true", "[ND]", "75001", "PARIS", diff="N"),
]


def write_csv(path: Path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def changed_since(self, since):
        yield from self.rows


def flat(ul_row, et_row):
    return {**ul_row, **et_row}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in ("000_reference_data.sql", "001_schema.sql", "002_staging.sql", "003_pipeline_state.sql",
                  "004_export_view.sql", "006_closure_date.sql"):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
    tmp = tmp_path_factory.mktemp("sirene")
    write_csv(tmp / "ul.csv", UL_COLUMNS, UL_ROWS)
    write_csv(tmp / "et.csv", ETAB_COLUMNS, ET_ROWS)
    sirene_loader.load(tmp / "ul.csv", tmp / "et.csv", only_active=True, dsn=DSN)
    return tmp


def q(sql, params=None):
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        return conn.execute(sql, params or ()).fetchall()


def test_bulk_load_counts_and_exclusions(db):
    assert {r["siren"] for r in q("SELECT siren FROM companies")} >= {S1, S2, S3}
    non_diff = q("SELECT 1 FROM company_addresses WHERE siret = %s", (T4,))
    assert non_diff == []                                  # non-diffusible establishment never lands in the master
    assert q("SELECT count(*) AS n FROM company_addresses")[0]["n"] == 4
    hq = q("SELECT a.siret FROM company_addresses a JOIN companies c USING (company_id) WHERE c.siren = %s AND a.is_head_office", (S1,))
    assert [r["siret"] for r in hq] == [T1_HQ]            # two establishments, one head office
    assert q("SELECT value FROM pipeline_state WHERE key = 'sirene_last_processed'")[0]["value"] >= "2026-10-01"


def test_artisan_flag_and_department_derivation(db):
    assert q("SELECT is_artisan FROM companies WHERE siren = %s", (S1,))[0]["is_artisan"] is True
    dep = {r["siret"]: r["department_code"] for r in q("SELECT siret, department_code FROM company_addresses")}
    assert dep[T1_HQ] == "69" and dep[T2] == "13" and dep[T3] == "42"


def test_weekly_delta_records_changes_closures_and_new_records(db):
    new_siren = siren(99887766)
    new_siret = siret(new_siren, 1)
    rows = [
        # address change on the Lyon head office
        flat(ul(S1, "BOULANGERIE DUPONT", processed="2026-10-08T09:00:00"),
             et(S1, T1_HQ, 1, "true", "NOUVELLE RUE", "69002", "LYON 2EME", rm="1071C", processed="2026-10-08T09:00:00")),
        # closure of the Marseille cafe
        flat(ul(S2, "CAFE DE L'ETOILE MULLER", naf="56.30Z", state="C", processed="2026-10-08T09:00:00"),
             et(S2, T2, 1, "true", "DE LA REPUBLIQUE", "13001", "MARSEILLE 1ER", state="F", naf="56.30Z", processed="2026-10-08T09:00:00")),
        # untouched plumber re-sent by the API (must count as unchanged)
        flat(ul(S3, "PLOMBERIE SAINT-ETIENNE", naf="43.22A", processed="2026-10-08T09:00:00"),
             et(S3, T3, 1, "true", "JEAN JAURES", "42000", "SAINT-ETIENNE", naf="43.22A", processed="2026-10-08T09:00:00")),
        # brand-new company
        flat(ul(new_siren, "ELECTRICITE NOUVELLE", naf="43.21A", processed="2026-10-09T09:00:00"),
             et(new_siren, new_siret, 1, "true", "DES LILAS", "33000", "BORDEAUX", naf="43.21A", processed="2026-10-09T09:00:00")),
    ]
    stats = weekly.run("2026-10-02T00:00:00", DSN, client=FakeClient(rows))
    assert stats["added_companies"] == 1 and stats["added_establishments"] == 1
    assert stats["closed"] >= 1 and stats["updated"] >= 2
    hist = q("SELECT field_name, old_value, new_value FROM company_change_history ORDER BY change_id")
    fields = {(h["field_name"], h["old_value"], h["new_value"]) for h in hist}
    assert any(f[0] in ("postal_code", "street", "city") for f in fields)       # address change recorded with old/new
    assert any(f[0] == "status" and f[2] == "closed" for f in fields)            # closure recorded
    assert q("SELECT status FROM company_addresses WHERE siret = %s", (T2,))[0]["status"] == "closed"
    assert q("SELECT value FROM pipeline_state WHERE key = 'sirene_last_processed'")[0]["value"] == "2026-10-09T09:00:00"
    # a second identical run must not create duplicates
    weekly.run("2026-10-02T00:00:00", DSN, client=FakeClient(rows))
    assert q("SELECT count(*) AS n FROM companies WHERE siren = %s", (new_siren,))[0]["n"] == 1
    assert q("SELECT count(*) AS n FROM company_addresses")[0]["n"] == 5


def test_reference_data_seeded(db):
    assert q("SELECT count(*) AS n FROM department_reference")[0]["n"] == 101      # 96 metropolitan + 5 overseas
    assert q("SELECT count(DISTINCT region_code) AS n FROM department_reference")[0]["n"] == 18
    assert q("SELECT count(*) AS n FROM employee_range_reference")[0]["n"] == 16   # INSEE trancheEffectifs
    corsica = q("SELECT code, region_name FROM department_reference WHERE code IN ('2A','2B') ORDER BY code")
    assert [r["region_name"] for r in corsica] == ["Corse", "Corse"]
    assert q("SELECT region_name FROM department_reference WHERE code = '974'")[0]["region_name"] == "La Réunion"


def test_view_fills_department_region_and_legal_form(db):
    v = {r["siret"]: r for r in q("SELECT * FROM v_hubspot_companies")}
    assert v[T1_HQ]["department_name"] == "Rhône" and v[T1_HQ]["region"] == "Auvergne-Rhône-Alpes"
    assert v[T2]["department_name"] == "Bouches-du-Rhône" and v[T2]["region"] == "Provence-Alpes-Côte d'Azur"
    assert v[T3]["department_name"] == "Loire"
    assert v[T1_HQ]["legal_form"].startswith("SARL")          # 5499 is in the curated subset


def test_view_unknown_codes_fall_back_to_raw_code_not_a_guess(db):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE companies SET legal_form_code = '9999', employee_range = '99' WHERE siren = %s", (S3,))
    v = {r["siret"]: r for r in q("SELECT * FROM v_hubspot_companies")}
    assert v[T3]["legal_form"] == "9999" and v[T3]["employee_range"] == "99"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE companies SET legal_form_code = '5499', employee_range = NULL WHERE siren = %s", (S3,))


def test_closure_dates_observed_exact_and_cleared_on_reopen(db):
    # T2/S2 were closed by the delta above without an exact INSEE date -> the date INSEE processed it, marked observed
    r = q("SELECT closure_date::text AS d, closure_date_basis AS b FROM company_addresses WHERE siret = %s", (T2,))[0]
    assert (r["d"], r["b"]) == ("2026-10-08", "observed")
    c = q("SELECT closure_date::text AS d, closure_date_basis AS b FROM companies WHERE siren = %s", (S2,))[0]
    assert (c["d"], c["b"]) == ("2026-10-08", "observed")
    assert q("SELECT count(*) AS n FROM company_addresses WHERE status = 'active' AND closure_date IS NOT NULL")[0]["n"] == 0
    # the identical run done in the previous test did not move the date
    assert q("SELECT count(*) AS n FROM company_change_history WHERE change_type = 'close' AND establishment_id = "
             "(SELECT establishment_id FROM company_addresses WHERE siret = %s)", (T2,))[0]["n"] == 1

    # an API period that began with the closure carries the exact date -> basis 'insee'
    exact = {"changementEtatAdministratifEtablissement": "true", "dateDebutPeriodeEtablissement": "2026-10-10"}
    rows = [flat(ul(S3, "PLOMBERIE SAINT-ETIENNE", naf="43.22A", state="C", processed="2026-10-12T09:00:00"),
                 {**et(S3, T3, 1, "true", "JEAN JAURES", "42000", "SAINT-ETIENNE", state="F", naf="43.22A",
                       processed="2026-10-12T09:00:00"), **exact})]
    weekly.run("2026-10-11T00:00:00", DSN, client=FakeClient(rows))
    r = q("SELECT closure_date::text AS d, closure_date_basis AS b FROM company_addresses WHERE siret = %s", (T3,))[0]
    assert (r["d"], r["b"]) == ("2026-10-10", "insee")
    weekly.run("2026-10-11T00:00:00", DSN, client=FakeClient(rows))                      # idempotent
    assert q("SELECT closure_date::text AS d FROM company_addresses WHERE siret = %s", (T3,))[0]["d"] == "2026-10-10"

    # reopened: the closure date is cleared, the reopening is in the history
    rows = [flat(ul(S3, "PLOMBERIE SAINT-ETIENNE", naf="43.22A", processed="2026-10-13T09:00:00"),
                 et(S3, T3, 1, "true", "JEAN JAURES", "42000", "SAINT-ETIENNE", naf="43.22A", processed="2026-10-13T09:00:00"))]
    weekly.run("2026-10-12T00:00:00", DSN, client=FakeClient(rows))
    r = q("SELECT status, closure_date, closure_date_basis FROM company_addresses WHERE siret = %s", (T3,))[0]
    assert r["status"] == "active" and r["closure_date"] is None and r["closure_date_basis"] is None
    assert q("SELECT count(*) AS n FROM company_change_history WHERE change_type = 'reopen'")[0]["n"] >= 1


def test_hubspot_export_from_view(db, tmp_path):
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        with conn.cursor(name="c", row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM v_hubspot_companies ORDER BY siren, is_head_office DESC, siret")
            res = export_companies(cur, tmp_path, include_closed=True)
    assert res["exported"] >= 4
    with open(res["files"][0], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_siret = {r["SIRET"]: r for r in rows}
    assert T4 not in by_siret                                             # non-diffusible never exported
    assert by_siret[T1_HQ]["Siege"].lower() in ("oui", "true", "1", "yes")
    assert by_siret[T2]["Statut etablissement"] == "closed"
    assert by_siret[T2]["Date cloture"] == "2026-10-08" and by_siret[T1_HQ]["Date cloture"] == ""
    # geography columns the client filters on are now filled from department_reference
    assert by_siret[T1_HQ]["Region"] == "Auvergne-Rhône-Alpes" and by_siret[T1_HQ]["State/Region"] == "Auvergne-Rhône-Alpes"
    assert by_siret[T1_HQ]["Departement"] == "Rhône" and by_siret[T1_HQ]["Departement code"] == "69"
    assert by_siret[T1_HQ]["Forme juridique"].startswith("SARL")
    assert "CAFE DE L'ETOILE MULLER" in {r["Company name"] for r in rows} or any("ETOILE" in r["Company name"] for r in rows)
