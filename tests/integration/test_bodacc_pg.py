"""BODACC loader against a REAL PostgreSQL (TEST_DATABASE_URL), API stubbed."""
import os
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.enrich import bodacc  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name, is_diffusible, status) VALUES ('552100554','ALPHA',TRUE,'active')")


def fake(params):
    fam = "collective" if "collective" in params["where"] else "radiation"
    rows = {"collective": [{"id": "A1", "registre": ["552 100 554"], "dateparution": "2026-10-12", "familleavis": "collective"}],
            "radiation": [{"id": "B1", "registre": ["999 999 999"], "dateparution": "2026-10-12", "familleavis": "radiation"}]}[fam]
    return {"total_count": 1, "results": rows if "2026-10-12" in params["where"] else []}


def test_idempotent_and_only_announcements_about_known_siren_are_kept(db):
    s1 = bodacc.run(DSN, days=2, today=date(2026, 10, 13), get=fake)
    s2 = bodacc.run(DSN, days=2, today=date(2026, 10, 13), get=fake)
    assert s1["stored"] == 1 and s1["issues"] == 1 and s1["skipped_unknown"] == 1   # the 999 999 999 announcement is not about a company we hold
    assert s2["stored"] == 0 and s2["issues"] == 0
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT count(*) FROM bodacc_events").fetchone()[0] == 1
        assert c.execute("SELECT issue_code FROM data_quality_issues").fetchall() == [("BODACC_COLLECTIVE_PROCEDURE",)]
        assert c.execute("SELECT status FROM companies").fetchone()[0] == "active"


def test_only_known_false_keeps_every_announcement(db):
    s = bodacc.run(DSN, days=2, today=date(2026, 10, 13), get=fake, only_known=False)
    assert s["stored"] == 2
