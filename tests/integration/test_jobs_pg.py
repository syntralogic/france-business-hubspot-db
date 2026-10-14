"""job_runs recording and staleness check against a REAL PostgreSQL (TEST_DATABASE_URL)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fbdb.ops import jobs  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def db(monkeypatch):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
    alerts = []
    monkeypatch.setattr(jobs, "send_alert", lambda s, b="": alerts.append(s) or {})
    return alerts


def test_never_ok_failed_and_stale(db):
    alerts = db
    assert jobs.check(DSN, "weekly")[0] == "never" and len(alerts) == 1
    start = datetime.now(timezone.utc)
    jobs.record(DSN, "weekly", "ok", start)
    assert jobs.check(DSN, "weekly")[0] == "ok" and len(alerts) == 1          # healthy: no alert
    jobs.record(DSN, "weekly", "partial", start, "geocode", "reports/run.log")
    state, msg = jobs.check(DSN, "weekly")
    assert state == "failed" and "geocode" in msg and len(alerts) == 2
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE job_runs SET finished_at = now() - interval '10 days'")
        c.commit()
    assert jobs.check(DSN, "weekly")[0] == "stale" and len(alerts) == 3
    assert jobs.check(DSN, "other-job")[0] == "never"
