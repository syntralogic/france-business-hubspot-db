"""Failure alerts: Slack incoming webhook and/or SMTP email, plus an optional dead-man's-switch ping.

Configure any of these in .env (unset channels are skipped; with none set the alert is only logged):
  SLACK_WEBHOOK_URL                       Slack incoming webhook
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_FROM, ALERT_EMAIL_TO (comma separated)
  HEALTHCHECK_PING_URL                    e.g. a healthchecks.io check URL: pinged on success, "/fail" appended on failure.
                                          An EXTERNAL check is the only way to notice a server that is down or cron that never fired.
send_alert() never raises: a broken alert channel must not hide or replace the original failure.

Usage: python -m fbdb.ops.notify --subject "..." [--body "..." | --body-file path] [--tail 40]
       python -m fbdb.ops.notify --ping ok|fail
"""
from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("fbdb.notify")


def _slack(subject: str, body: str) -> None:
    url = os.environ["SLACK_WEBHOOK_URL"]
    r = httpx.post(url, json={"text": f"*{subject}*\n```{body[:3000]}```"}, timeout=15.0)
    r.raise_for_status()


def _email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, os.environ["ALERT_EMAIL_FROM"]
    msg["To"] = ", ".join(x.strip() for x in os.environ["ALERT_EMAIL_TO"].split(",") if x.strip())
    msg.set_content(body)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def send_alert(subject: str, body: str = "") -> dict:
    """Try every configured channel. Returns {channel: True/False}; unconfigured channels are absent."""
    sent: dict[str, bool] = {}
    channels = []
    if os.environ.get("SLACK_WEBHOOK_URL"):
        channels.append(("slack", _slack))
    if os.environ.get("SMTP_HOST") and os.environ.get("ALERT_EMAIL_TO") and os.environ.get("ALERT_EMAIL_FROM"):
        channels.append(("email", _email))
    for name, fn in channels:
        try:
            fn(subject, body)
            sent[name] = True
        except Exception as exc:  # noqa: BLE001 - alerting must never raise
            sent[name] = False
            log.error("alert via %s failed: %s", name, exc)
    if not channels:
        log.error("ALERT (no channel configured): %s | %s", subject, body[:500])
    return sent


def ping(ok: bool = True) -> bool:
    url = os.environ.get("HEALTHCHECK_PING_URL", "").strip()
    if not url:
        return False
    try:
        httpx.get(url if ok else url.rstrip("/") + "/fail", timeout=10.0).raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("healthcheck ping failed: %s", exc)
        return False


def tail_file(path: Optional[str], n: int) -> str:
    if not path or not Path(path).is_file():
        return ""
    return "".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-n:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject")
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file")
    ap.add_argument("--tail", type=int, default=40, help="last N lines of --body-file")
    ap.add_argument("--ping", choices=["ok", "fail"])
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.ping:
        ping(a.ping == "ok")
    if a.subject:
        body = a.body or tail_file(a.body_file, a.tail)
        print(send_alert(a.subject, body))
    elif not a.ping:
        ap.error("give --subject and/or --ping")


if __name__ == "__main__":
    main()
