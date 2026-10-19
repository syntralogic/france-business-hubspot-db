"""Legal relations and register events against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import register_events  # noqa: E402
from fbdb.enrich.annuaire import map_legal_entity_officers  # noqa: E402
from fbdb.enrich.company_relations import refresh_relations  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            c.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        yield c


def _company(c, siren, name="X"):
    return c.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES (%s,%s,TRUE,'active') RETURNING company_id",
                     (siren, name)).fetchone()[0]


def _est(c, company_id, siret, created, head=False, city="LYON"):
    c.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, status, creation_date, postal_code, city) "
              "VALUES (%s,%s,%s,'active',%s,'69001',%s)", (company_id, siret, head, created, city))


def test_relations_lifecycle(conn):
    child, parent = _company(conn, "552100554", "CHILD"), _company(conn, "552081317", "HOLDING")
    sid = conn.execute("SELECT source_id FROM data_sources WHERE code='annuaire'").fetchone()[0]
    result = {"siren": "552100554", "dirigeants": [{"type_dirigeant": "personne morale", "siren": "552081317", "denomination": "HOLDING SA",
                                                      "qualite": "Président"}]}
    with conn.cursor() as cur:
        assert refresh_relations(cur, child, map_legal_entity_officers(result), sid, None) == 1
        assert refresh_relations(cur, child, map_legal_entity_officers(result), sid, None) == 0      # idempotent
    row = conn.execute("SELECT related_name, related_in_base, role, is_active FROM v_company_relations WHERE company_id=%s", (child,)).fetchone()
    assert row == ("HOLDING", True, "Président", True)                                              # name from our own base
    with conn.cursor() as cur:
        assert refresh_relations(cur, child, [], sid, None) == 1                                      # no longer listed
    assert conn.execute("SELECT is_active, ended_on IS NOT NULL FROM company_relations").fetchone() == (False, True)
    with conn.cursor() as cur:
        assert refresh_relations(cur, child, map_legal_entity_officers(result), sid, None) == 1       # back
    assert conn.execute("SELECT is_active, ended_on FROM company_relations").fetchone() == (True, None)
    assert conn.execute("SELECT count(*) FROM company_change_history WHERE table_name='company_relations'").fetchone()[0] == 3
    assert parent  # the holding exists in the base


def test_register_events(conn):
    c1 = _company(conn, "552100554")
    _est(conn, c1, "55210055400005", "2015-01-01", head=True)
    _est(conn, c1, "55210055400013", "2026-08-01", city="NICE")      # recent branch of an existing company -> event
    c2 = _company(conn, "552081317")
    _est(conn, c2, "55208131700010", "2026-08-01", head=True)         # brand new company: no event (head office, no older site)
    c3 = _company(conn, "775665912")
    _est(conn, c3, "77566591200010", "2010-01-01", head=True)
    _est(conn, c3, "77566591200028", "2020-01-01")                    # old branch: outside the window
    conn.execute("INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type) "
                 "VALUES (%s,'companies','main_naf','43.21A','43.22A','update')", (c1,))
    conn.execute("INSERT INTO bodacc_events (bodacc_id, siren, family, family_label, avis_type, published_on) VALUES "
                 "('M1','552081317','modification','Modifications diverses',%s,current_date),"
                 "('M2','552081317','modification','Modifications diverses','Changement de dirigeant',current_date),"
                 "('R1','552081317','radiation','Radiations',%s,current_date)", ("Ouverture d'un établissement secondaire", "Création d'établissement"))
    s = register_events.run(DSN)
    assert s["new_establishment"] == 1 and s["activity_change"] == 1 and s["bodacc_establishment"] == 1 and s["bodacc_activity"] == 0
    rows = conn.execute("SELECT event_type, detail FROM company_register_events ORDER BY event_type, detail").fetchall()
    assert ("new_establishment", "69001 NICE") in rows and ("activity_change", "43.21A -> 43.22A") in rows
    assert register_events.run(DSN) == {**s, "new_establishment": 0, "activity_change": 0, "bodacc_establishment": 0}   # idempotent


def test_mass_reclassification_is_not_activity(conn, monkeypatch):
    monkeypatch.setattr(register_events, "MASS_CHANGE_MIN", 2)
    ids = [_company(conn, s) for s in ("552100554", "552081317", "775665912")]
    batch = conn.execute("INSERT INTO import_export_batches (batch_type) VALUES ('import') RETURNING batch_id").fetchone()[0]
    for i in ids:
        conn.execute("INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, batch_id) "
                     "VALUES (%s,'companies','main_naf','43.21A','43.21B','update',%s)", (i, batch))
    s = register_events.run(DSN)
    assert s["activity_change"] == 0 and s["mass_batches_ignored"] == 1
