"""Record scheduled runs and check that the weekly job really ran (monitoring: "job chala ya nahi").

  record: called at the end of scripts/run_weekly.sh, stores one row in job_runs.
  check : run DAILY from cron. Raises an alert (Slack/email) and exits 1 when
            - no run at all, or the newest run is older than --max-age-days (default 8: weekly + 1 day of slack), or
            - the newest run failed or was partial.
          This catches the case the job itself cannot report: cron never fired, or the server was off.

Usage: python -m fbdb.ops.jobs record --job weekly --status ok|partial|failed --started ISO [--failed-steps a,b] [--log path]
       python -m fbdb.ops.jobs check --job weekly [--max-age-days 8]
Needs: DATABASE_URL, sql/011_ops.sql applied.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import psycopg

from fbdb.ops.notify import send_alert

log = logging.getLogger("fbdb.jobs")


def record(dsn: str, job: str, status: str, started: datetime, failed_steps: str = "", log_path: Optional[str] = None) -> int:
    with psycopg.connect(dsn) as conn:
        rid = conn.execute(
            "INSERT INTO job_runs (job, started_at, status, failed_steps, log_path) VALUES (%s,%s,%s,%s,%s) RETURNING run_id",
            (job, started, status, failed_steps or None, log_path)).fetchone()[0]
        conn.commit()
    return rid


def evaluate(last: Optional[tuple], max_age_days: float, now: Optional[datetime] = None) -> tuple[str, str]:
    """last = (status, finished_at, failed_steps) of the newest run or None. Returns (state, message), state in ok/stale/failed/never."""
    now = now or datetime.now(timezone.utc)
    if last is None:
        return "never", "no run has ever been recorded"
    status, finished, failed = last
    age = (now - finished).total_seconds() / 86400
    if age > max_age_days:
        return "stale", f"last run finished {age:.1f} days ago (limit {max_age_days:g}), status {status}"
    if status != "ok":
        return "failed", f"last run status {status}; failed steps: {failed or 'unknown'}"
    return "ok", f"last run ok, {age:.1f} days ago"


def check(dsn: str, job: str, max_age_days: float = 8.0) -> tuple[str, str]:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT status, finished_at, failed_steps FROM job_runs WHERE job=%s ORDER BY finished_at DESC LIMIT 1",
                           (job,)).fetchone()
    state, msg = evaluate(row, max_age_days)
    if state != "ok":
        send_alert(f"FBDB monitoring: job '{job}' {state}", msg)
    return state, msg


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--job", required=True)
    r.add_argument("--status", required=True, choices=["ok", "partial", "failed"])
    r.add_argument("--started", required=True, help="ISO 8601, e.g. 2026-10-12T03:00:01Z")
    r.add_argument("--failed-steps", default="")
    r.add_argument("--log")
    c = sub.add_parser("check")
    c.add_argument("--job", required=True)
    c.add_argument("--max-age-days", type=float, default=8.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    dsn = os.environ["DATABASE_URL"]
    if a.cmd == "record":
        print(record(dsn, a.job, a.status, datetime.fromisoformat(a.started.replace("Z", "+00:00")), a.failed_steps, a.log))
    else:
        state, msg = check(dsn, a.job, a.max_age_days)
        print(state, msg)
        raise SystemExit(0 if state == "ok" else 1)


if __name__ == "__main__":
    main()
