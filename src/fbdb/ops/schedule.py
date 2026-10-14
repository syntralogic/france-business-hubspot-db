"""Small in-container scheduler for hosts that have no cron next to the container (Render background worker).

Runs, in UTC:
  weekly : scripts/run_weekly.sh                         on WEEKLY_DAY at WEEKLY_TIME   (default mon 03:00)
  check  : python -m fbdb.ops.jobs check --job weekly    every day at CHECK_TIME        (default 09:30)
Jobs run one at a time. If a job is still running when another is due, the due one is skipped (next occurrence is used).
Env: WEEKLY_DAY (mon..sun), WEEKLY_TIME (HH:MM), CHECK_TIME (HH:MM), RUN_WEEKLY_ON_START (1 = run the weekly job once at start).
SIGTERM (Render deploy/restart): the running job is terminated and the scheduler exits.
Usage: python -m fbdb.ops.schedule
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("fbdb.schedule")

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.strip().split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"time must be HH:MM, got {value!r}") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return h, m


def parse_day(value: str) -> int:
    key = value.strip().lower()[:3]
    if key not in DAYS:
        raise ValueError(f"day must be one of {sorted(DAYS)}, got {value!r}")
    return DAYS[key]


def next_weekly(now: datetime, weekday: int, hh: int, mm: int) -> datetime:
    """First moment strictly after `now` that falls on `weekday` at hh:mm (same tz as now)."""
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=(weekday - now.weekday()) % 7)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def next_daily(now: datetime, hh: int, mm: int) -> datetime:
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)
    return cand


class Scheduler:
    def __init__(self) -> None:
        self.weekday = parse_day(os.environ.get("WEEKLY_DAY", "mon"))
        self.weekly_hm = parse_hhmm(os.environ.get("WEEKLY_TIME", "03:00"))
        self.check_hm = parse_hhmm(os.environ.get("CHECK_TIME", "09:30"))
        self.stop = False
        self.child: Optional[subprocess.Popen] = None

    def _on_term(self, signum, frame) -> None:  # noqa: ARG002
        log.warning("signal %s: stopping", signum)
        self.stop = True
        if self.child and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run_job(self, name: str, cmd: list[str]) -> int:
        log.info("START %s: %s", name, " ".join(cmd))
        self.child = subprocess.Popen(cmd, start_new_session=True)
        rc = self.child.wait()
        self.child = None
        log.info("END   %s: exit %s", name, rc)
        return rc

    def due_times(self, now: datetime) -> dict[str, datetime]:
        return {"weekly": next_weekly(now, self.weekday, *self.weekly_hm), "check": next_daily(now, *self.check_hm)}

    def commands(self) -> dict[str, list[str]]:
        return {
            "weekly": ["bash", "scripts/run_weekly.sh"],
            "check": [sys.executable, "-m", "fbdb.ops.jobs", "check", "--job", "weekly", "--max-age-days", "8"],
        }

    def loop(self) -> None:
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)
        cmds = self.commands()
        if os.environ.get("RUN_WEEKLY_ON_START") == "1":
            self.run_job("weekly", cmds["weekly"])
        due = self.due_times(datetime.now(timezone.utc))
        log.info("next runs (UTC): %s", {k: v.isoformat(timespec="minutes") for k, v in due.items()})
        while not self.stop:
            name, when = min(due.items(), key=lambda kv: kv[1])
            now = datetime.now(timezone.utc)
            if now < when:
                time.sleep(min(30.0, (when - now).total_seconds()))
                continue
            self.run_job(name, cmds[name])
            if self.stop:
                break
            due = self.due_times(datetime.now(timezone.utc))   # skip anything missed while a job was running
            log.info("next runs (UTC): %s", {k: v.isoformat(timespec="minutes") for k, v in due.items()})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    Scheduler().loop()


if __name__ == "__main__":
    main()
