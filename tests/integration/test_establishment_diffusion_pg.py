"""An establishment that INSEE stops diffusing leaves the export and the providers; diffused again it comes back. REAL PostgreSQL."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import geocode, places  # noqa: E402
from fbdb.load import weekly  # noqa: E402
from test_pipeline_pg import FakeClient, et, flat, siren, siret, ul  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
S = siren(55210055)
HQ, BR = siret(S, 1), siret(S, 2)


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))


def run(branch_diff="O", branch_street="DES ECOLES", processed="2026-10-08T09:00:00"):
    rows = [flat(ul(S, "BOULANGERIE DUPONT", processed=processed),
                 et(S, HQ, 1, "true", "DE LA PAIX", "69003", "LYON 3EME", rm="1071C", processed=processed)),
            flat(ul(S, "BOULANGERIE DUPONT", processed=processed),
                 et(S, BR, 2, "false", branch_street, "69100" if branch_diff == "O" else "", "VILLEURBANNE" if branch_diff == "O" else "[ND]",
                    diff=branch_diff, processed=processed))]
    return weekly.run("2026-10-01T00:00:00", DSN, client=FakeClient(rows))


def q(sql, *a):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, a).fetchall()


def exported():
    return {r[0] for r in q("SELECT siret FROM v_hubspot_companies")}


def test_establishment_no_longer_diffused_is_hidden_kept_and_comes_back_when_diffused_again(db):
    run()
    assert exported() == {HQ, BR}
    run(branch_diff="N", branch_street="[ND]", processed="2026-10-15T09:00:00")
    assert q("SELECT is_diffusible FROM company_addresses WHERE siret=%s", BR) == [(False,)]
    assert exported() == {HQ}                                                    # hidden from the HubSpot export
    kept = q("SELECT street, city FROM company_addresses WHERE siret=%s", BR)
    assert len(kept) == 1 and "ECOLES" in kept[0][0] and kept[0][1] == "VILLEURBANNE"   # nothing deleted, not overwritten by the masked values
    run(branch_diff="O", branch_street="DU PARC", processed="2026-10-22T09:00:00")
    assert q("SELECT is_diffusible FROM company_addresses WHERE siret=%s", BR) == [(True,)]
    assert exported() == {HQ, BR}
    assert "DU PARC" in q("SELECT street FROM company_addresses WHERE siret=%s", BR)[0][0]       # fields refreshed too


def test_providers_never_receive_a_non_diffusible_establishment(db):
    run()
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE company_addresses SET address_status='verified', latitude=45.7, longitude=4.8, geocoded_at=now()")
    run(branch_diff="N", branch_street="[ND]", processed="2026-10-15T09:00:00")

    class Fake:
        def __init__(self):
            self.queries = []

        def search(self, query, lat, lon):
            self.queries.append(query)
            return []
    fake = Fake()
    places.run(DSN, fake, limit=10, cap=10)
    assert len(fake.queries) == 1 and "LYON" in fake.queries[0] and "VILLEURBANNE" not in fake.queries[0]
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("UPDATE company_addresses SET geocoded_at=NULL, address_status='unverified'")
    seen = []
    geocode.run(DSN, post=lambda text: (seen.append(text), "id,latitude\n")[1])
    assert len(seen[0].splitlines()) == 2 and "VILLEURBANNE" not in seen[0]                  # header + the diffusible one only


def test_003c_is_idempotent_and_defaults_existing_rows_to_diffusible(db):
    run()
    with psycopg.connect(DSN, autocommit=True) as c:
        for _ in range(2):
            c.execute((SQL_DIR / "003c_address_diffusion.sql").read_text(encoding="utf-8"))
        assert c.execute("SELECT count(*) FROM company_addresses WHERE is_diffusible").fetchone()[0] == 2
