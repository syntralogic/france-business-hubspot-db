"""DECP / ADEME automatic download against a REAL PostgreSQL, with a fake HTTP transport (no network)."""
import os
from pathlib import Path

import httpx
import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import ademe_rge, decp  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
A, B = "552100554", "401234562"
A_SIRET, B_SIRET = "55210055400013", "40123456200015"
CSV_URL = "https://static.data.gouv.fr/resources/decp/decp.csv"
CSV = ("uid,id,acheteur_id,acheteur_nom,titulaire_id,titulaire_typeIdentifiant,titulaire_denominationSociale,objet,montant,dateNotification,donneesActuelles\n"
       f"U1,M1,2169,Mairie,{A_SIRET},SIRET,ALPHA,Travaux,45000,2025-01-10,true\n"
       f"U1,M1,2169,Mairie,{A_SIRET},SIRET,ALPHA,Travaux,40000,2025-01-10,false\n"      # an older version of the same market
       f"U2,M2,2169,Mairie,{B_SIRET},SIRET,BETA,Fournitures,1200,2024-12-01,true\n")


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name) VALUES (%s,'ALPHA ELEC'), (%s,'BETA SARL')", (A, B))
    yield


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def fake_datagouv():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.path.endswith("/datasets/ds1/"):
            return httpx.Response(200, json={"title": "DECP tabulaire", "page": "https://www.data.gouv.fr/datasets/ds1", "resources": [
                {"title": "decp.parquet", "format": "parquet", "type": "main", "url": "https://static/decp.parquet", "last_modified": "2026-10-10"},
                {"title": "decp.csv", "format": "csv", "type": "main", "url": CSV_URL, "last_modified": "2026-10-12T03:00:00"}]})
        if str(req.url) == CSV_URL:
            return httpx.Response(200, content=CSV.encode(), headers={"content-type": "text/csv"})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), calls


def test_decp_download_loads_current_rows_with_the_resource_url_as_proof_and_removes_the_file(db, tmp_path):
    c, _ = fake_datagouv()
    s = decp.download_and_run(DSN, "ds1", work_dir=str(tmp_path), rejects_path=str(tmp_path / "rej.csv"), client=c)
    assert s["loaded"] == 2 and s["downloaded_bytes"] == len(CSV.encode())             # the false row is not read, not a conflict
    assert q("SELECT decp_uid, amount_eur, source_url FROM public_contracts ORDER BY decp_uid") == [("U1", 45000, CSV_URL), ("U2", 1200, CSV_URL)]
    assert q("SELECT count(*) FROM field_conflicts")[0][0] == 0
    label, url, note = q("SELECT source_label, source_url, coverage_note FROM decp_loads")[0]
    assert "2026-10-12" in label and url == CSV_URL and "NOT necessarily never worked" in note
    assert [p for p in tmp_path.iterdir() if p.suffix in (".csv", ".part") and p.name != "rej.csv"] == []


def test_decp_if_older_than_days_skips_a_recent_load_and_does_not_even_call_the_api(db, tmp_path):
    c, calls = fake_datagouv()
    decp.download_and_run(DSN, "ds1", work_dir=str(tmp_path), rejects_path=str(tmp_path / "r.csv"), client=c, if_older_than_days=30)
    n_calls = len(calls)
    s = decp.download_and_run(DSN, "ds1", work_dir=str(tmp_path), client=c, if_older_than_days=30)
    assert "skipped" in s and len(calls) == n_calls and q("SELECT count(*) FROM decp_loads")[0][0] == 1
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE decp_loads SET loaded_at = now() - interval '40 days'")
    s = decp.download_and_run(DSN, "ds1", work_dir=str(tmp_path), rejects_path=str(tmp_path / "r.csv"), client=c, if_older_than_days=30)
    assert s["loaded"] == 0 and s["duplicate"] == 2                                      # same contracts again: duplicates, no conflict
    assert q("SELECT count(*) FROM decp_loads")[0][0] == 2


def test_decp_download_failure_leaves_nothing_loaded(db, tmp_path):
    c = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        decp.download_and_run(DSN, "ds1", work_dir=str(tmp_path), client=c)
    assert q("SELECT count(*) FROM decp_loads")[0][0] == 0


def test_ademe_download_needs_a_url_and_never_guesses_one(db, tmp_path):
    with pytest.raises(ValueError, match="RGE_CSV_URL"):
        ademe_rge.download_and_run(DSN, None, str(tmp_path))
