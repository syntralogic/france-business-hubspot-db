import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fbdb.ops import notify
from fbdb.ops.jobs import evaluate

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def test_evaluate_states():
    assert evaluate(None, 8, NOW)[0] == "never"
    assert evaluate(("ok", NOW - timedelta(days=3), None), 8, NOW)[0] == "ok"
    assert evaluate(("ok", NOW - timedelta(days=9), None), 8, NOW)[0] == "stale"
    state, msg = evaluate(("partial", NOW - timedelta(days=1), "geocode"), 8, NOW)
    assert state == "failed" and "geocode" in msg


def test_send_alert_channels_and_isolation(monkeypatch):
    for k in ("SLACK_WEBHOOK_URL", "SMTP_HOST", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    assert notify.send_alert("s", "b") == {}                      # nothing configured: logged only, no error
    posted = []

    class R:
        def raise_for_status(self):
            pass

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: posted.append((url, json)) or R())
    assert notify.send_alert("FAILED", "details") == {"slack": True}
    assert posted[0][0] == "https://hooks.example/x" and "FAILED" in posted[0][1]["text"]

    sent = []

    class SMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent.append(("msg", msg["Subject"], msg["To"]))

    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "a@x.fr")
    monkeypatch.setenv("ALERT_EMAIL_TO", "b@x.fr, c@x.fr")
    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    assert notify.send_alert("S2", "b") == {"slack": True, "email": True}
    assert ("msg", "S2", "b@x.fr, c@x.fr") in sent

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.send_alert("S3", "b") == {"slack": False, "email": True}   # one broken channel never raises


def test_ping_appends_fail(monkeypatch):
    urls = []

    class R:
        def raise_for_status(self):
            pass

    monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc.example/uuid/")
    monkeypatch.setattr(notify.httpx, "get", lambda url, timeout: urls.append(url) or R())
    assert notify.ping(True) and notify.ping(False)
    assert urls == ["https://hc.example/uuid/", "https://hc.example/uuid/fail"]
    monkeypatch.delenv("HEALTHCHECK_PING_URL")
    assert notify.ping(True) is False


def _run_script(tmp_path, failing: str):
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts" / "run_weekly.sh"
    script.write_text((Path(__file__).resolve().parents[1] / "scripts" / "run_weekly.sh").read_text())
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "python"
    calls = tmp_path / "calls.txt"
    shim.write_text(f'#!/usr/bin/env bash\necho "$@" >> {calls}\n[[ "$*" == *{failing}* ]] && exit 1\nexit 0\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    p = subprocess.run(["bash", str(script)], cwd=root, env=env, capture_output=True, text=True)
    return p, calls.read_text().splitlines()


def test_run_weekly_continues_after_failure_records_and_alerts(tmp_path):
    p, calls = _run_script(tmp_path, "fbdb.enrich.geocode")
    assert p.returncode == 1 and "failed: geocode" in p.stderr
    ran = " | ".join(calls)
    assert "fbdb.enrich.bodacc" in ran                                  # later steps still ran
    assert "fbdb.enrich.pappers" not in ran and "fbdb.enrich.places" not in ran   # no keys: skipped
    rec = next(c for c in calls if "fbdb.ops.jobs record" in c)
    assert "--status partial" in rec and "--failed-steps geocode" in rec
    assert any("fbdb.ops.notify --ping fail --subject" in c for c in calls)


def test_run_weekly_all_ok_pings_and_exits_zero(tmp_path):
    p, calls = _run_script(tmp_path, "__nothing__")
    assert p.returncode == 0
    assert any("--status ok" in c for c in calls) and any("--ping ok" in c for c in calls)
    assert not any("--subject" in c for c in calls)


def test_run_weekly_delta_failure_is_failed_status(tmp_path):
    p, calls = _run_script(tmp_path, "fbdb.load.weekly")
    assert p.returncode == 1 and any("--status failed" in c for c in calls)
