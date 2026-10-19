"""DECP public contracts against a REAL PostgreSQL (TEST_DATABASE_URL). Files are written to tmp_path; no network."""
import csv
import json
import os
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import decp  # noqa: E402
from fbdb.report import metrics  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B = "552100554", "401234562"               # in base, diffusible
A_SIRET, B_SIRET = "55210055400013", "40123456200015"      # Luhn-valid
URL = "https://www.data.gouv.fr/fr/datasets/donnees-essentielles-de-la-commande-publique-fichiers-consolidés/"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s,'ALPHA ELEC'), (%s,'BETA SARL')", (A, B))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible) VALUES ('732829320','PRIVEE EI', FALSE)")
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def market(muid, holders, **over):
    m = {"id": "M" + muid, "uid": muid, "acheteur": {"id": "21690123100011", "nom": "Mairie de Lyon"},
         "titulaires": holders, "objet": "Travaux", "montant": 45000, "dateNotification": "2025-01-10", "datePublicationDonnees": "2025-01-20",
         "codeCPV": "45310000", "procedure": "Procedure adaptee", "nature": "Marche", "dureeMois": 12,
         "lieuExecution": {"code": "69123", "nom": "Lyon"}}
    m.update(over)
    return m


def holder(siret, name="X", typ="SIRET"):
    return {"typeIdentifiant": typ, "id": siret, "denominationSociale": name}


def write(tmp_path, markets, name="decp.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"marches": markets}), encoding="utf-8")
    return str(p)


def test_load_attaches_contracts_by_siren_with_provenance_and_counts_everything_else(db, tmp_path):
    f = write(tmp_path, [
        market("U1", [holder(A_SIRET, "ALPHA ELEC"), holder(B_SIRET, "BETA")]),                 # a group: two titulaires
        market("U2", [holder("73282932000009", "PRIVEE")]),                                      # non diffusible: not stored
        market("U3", [holder("30000000700009")]),                                                # valid SIRET but not in our base
        market("U4", [holder("FR12345678901", typ="TVA")]),                                      # not a SIRET: rejected
        market("U5", [holder(A_SIRET)], id=None, uid=None),                                      # nothing to identify the market: rejected
    ])
    rej = tmp_path / "rej.csv"
    s = decp.run(DSN, f, URL, label="DECP test", coverage_note="test file", rejects_path=str(rej))
    assert s == {"read": 6, "loaded": 2, "duplicate": 0, "conflict": 0, "rejected": 2, "not_in_base": 2}
    rows = {r[0]: r[1:] for r in q("""SELECT c.siren, p.decp_uid, p.market_id, p.buyer_name, p.amount_eur, p.notification_date, p.co_holders,
                                           p.source_url, p.titulaire_name, d.code
                                    FROM public_contracts p JOIN companies c USING (company_id) JOIN data_sources d USING (source_id)""")}
    assert set(rows) == {A, B}
    assert rows[A] == ("U1", "MU1", "Mairie de Lyon", 45000, date(2025, 1, 10), 2, URL, "ALPHA ELEC", "decp")
    assert rows[B][0] == "U1" and rows[B][7] == "BETA"
    assert q("""SELECT rows_read, rows_loaded, rows_rejected, rows_not_in_base, notified_from, notified_to, coverage_note, source_url
                FROM decp_loads""") == [(6, 2, 2, 2, date(2025, 1, 10), date(2025, 1, 10), "test file", URL)]
    with open(rej, newline="", encoding="utf-8") as fh:
        assert sorted(r["reason"] for r in csv.DictReader(fh)) == ["INVALID_TITULAIRE_ID", "NO_UID"]


def test_reloading_the_same_file_changes_nothing_and_a_different_amount_is_a_conflict_not_an_overwrite(db, tmp_path):
    decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)])]), URL)
    again = decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)])], "again.json"), URL)
    assert again["loaded"] == 0 and again["duplicate"] == 1 and again["conflict"] == 0
    changed = decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)], montant=52000, dateNotification="2025-03-01")], "chg.json"), URL)
    assert changed["conflict"] == 1 and changed["loaded"] == 0
    assert q("SELECT amount_eur, notification_date FROM public_contracts") == [(45000, date(2025, 1, 10))]   # kept
    assert sorted(q("SELECT field_name, kept_value, other_value FROM field_conflicts")) == [
        ("decp:amount:U1", "45000.00", "52000.00"), ("decp:notification_date:U1", "2025-01-10", "2025-03-01")]
    assert q("SELECT count(*) FROM decp_loads")[0][0] == 3 and q("SELECT count(*) FROM public_contracts")[0][0] == 1


def test_suspect_amounts_are_stored_flagged_and_unknown_amounts_stay_null(db, tmp_path):
    decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)], montant=0), market("U2", [holder(A_SIRET)], montant=2e9),
                                   market("U3", [holder(A_SIRET)], montant=None), market("U4", [holder(A_SIRET)], montant=1234.5)]), URL)
    assert q("SELECT decp_uid, amount_eur, amount_suspect FROM public_contracts ORDER BY decp_uid") == [
        ("U1", 0, True), ("U2", 2000000000, True), ("U3", None, False), ("U4", 1234.5, False)]


def test_csv_file_is_loaded_like_json(db, tmp_path):
    p = tmp_path / "decp.csv"
    p.write_text("uid;id;acheteur_id;acheteur_nom;titulaire_id;titulaire_typeIdentifiant;titulaire_denominationSociale;objet;montant;dateNotification\n"
                 f"U1;M1;2169;Mairie;{A_SIRET};SIRET;ALPHA;Travaux;45000,5;2025-01-10\n"
                 f"U2;M2;2169;Mairie;{B_SIRET};SIRET;BETA;Fournitures;1 200;2024-12-01\n", encoding="utf-8")
    s = decp.run(DSN, str(p), URL)
    assert s["loaded"] == 2 and q("SELECT decp_uid, amount_eur FROM public_contracts ORDER BY decp_uid") == [("U1", 45000.5), ("U2", 1200)]


def test_absence_is_never_stored_and_status_reads_not_checked_none_in_loaded_datasets_found(db, tmp_path):
    def status():
        return dict(q("SELECT siren, public_contract_status FROM v_public_contract_status WHERE siren IN (%s,%s)", A, B))

    assert status() == {A: "not_checked", B: "not_checked"}                      # nothing loaded yet: "non recherche"
    decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)])]), URL)
    assert status() == {A: "found", B: "none_in_loaded_datasets"}                # B: only means "not in what we loaded"
    assert q("SELECT count(*) FROM public_contracts p JOIN companies c USING (company_id) WHERE c.siren=%s", B)[0][0] == 0   # no "no contract" row
    assert q("SELECT contracts, last_notification IS NOT NULL FROM v_public_contract_status WHERE siren=%s", A) == [(1, True)]


def test_source_url_is_mandatory_and_a_broken_file_loads_nothing_silently(db, tmp_path):
    with pytest.raises(ValueError):
        decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)])]), "")
    bad = tmp_path / "bad.json"
    bad.write_text('{"marches": [{"id": "M1", "uid": "U1"', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        decp.run(DSN, str(bad), URL)


def test_database_constraints_migration_and_kpi(db, tmp_path):
    decp.run(DSN, write(tmp_path, [market("U1", [holder(A_SIRET)])]), URL)
    with psycopg.connect(DSN, autocommit=True) as conn:
        cid = conn.execute("SELECT company_id FROM companies WHERE siren=%s", (A,)).fetchone()[0]
        with pytest.raises(psycopg.errors.UniqueViolation):                      # one row per company x DECP uid
            conn.execute("INSERT INTO public_contracts (company_id, siren, decp_uid, source_url) VALUES (%s,%s,'U1','u')", (cid, A))
        with pytest.raises(psycopg.errors.NotNullViolation):                     # no row without its dataset URL
            conn.execute("INSERT INTO public_contracts (company_id, siren, decp_uid) VALUES (%s,%s,'U9')", (cid, A))
        sql = (SQL_DIR / "018_public_contracts.sql").read_text(encoding="utf-8")
        conn.execute(sql)
        conn.execute(sql)
    assert q("SELECT count(*) FROM data_sources WHERE code='decp'")[0][0] == 1
    _, rates = metrics.collect(DSN, target_rows=10)
    assert rates["match_companies_with_public_contracts"] == 0.5               # A of the 2 diffusible companies
