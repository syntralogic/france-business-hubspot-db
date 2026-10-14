"""Tiny HTTP wrapper so the weekly pipeline can run on a Render FREE web service.

Render free tier only offers web services (cron jobs are paid), and a web service must listen on $PORT. This module
starts a minimal server (standard library only) with three endpoints:

  GET  /health            -> 200 "ok". Public. Use it as the Render health check and for keep-alive pings.
  POST /run?job=all       -> starts the job in the background and answers 202 at once (409 if one is running).
                             job = all | weekly | directors. Default all = scripts/run_weekly.sh (the full weekly run:
                             delta, directors, geocode, BODACC, ... , job_runs record, failure alert). weekly and
                             directors run just that one step.
  GET  /status            -> JSON with the state and the log tail of the last run.

/run and /status need the secret RUN_TOKEN, sent as header `X-Run-Token: <secret>` (preferred) or `?key=<secret>`.
If RUN_TOKEN is not set the two endpoints are disabled (503): the server never runs jobs without a secret.

Free-tier caveat: Render puts a free service to sleep after 15 minutes without INBOUND traffic. While a job is
running the server therefore pings its own public URL (RENDER_EXTERNAL_URL, set by Render) every 5 minutes so the
service stays awake until the job ends. An external scheduler (cron-job.org, GitHub Actions) must call POST /run
once a week: nothing inside a sleeping free service can wake itself up.

Start command on Render:  PYTHONPATH=src python -m fbdb.web
Environment: DATABASE_URL, INSEE_API_KEY, RUN_TOKEN (required); the optional keys and limits that scripts/run_weekly.sh reads.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("fbdb.web")
KEEPALIVE_EVERY_S = 300
LOG_TAIL_CHARS = 4000


def job_commands(job: str) -> list[tuple[str, list[str]]]:
    """`all` runs scripts/run_weekly.sh: the repo's own weekly runner (every step, job_runs record, failure alert)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    all_steps = ("all", ["bash", os.path.join(root, "scripts", "run_weekly.sh")])
    weekly = ("weekly", [sys.executable, "-m", "fbdb.load.weekly"])
    directors = ("directors", [sys.executable, "-m", "fbdb.enrich.directors", "--limit",
                               os.environ.get("DIRECTORS_PER_RUN", "5000")])
    return {"all": [all_steps], "weekly": [weekly], "directors": [directors]}[job]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def subprocess_runner(name: str, cmd: list[str]) -> tuple[int, str]:
    """Run one pipeline step; returns (exit code, tail of combined output)."""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))[-LOG_TAIL_CHARS:]


class JobManager:
    """Runs at most one job at a time in a background thread and remembers the last result."""

    def __init__(self, runner=subprocess_runner, keepalive=None):
        self._runner, self._keepalive = runner, keepalive
        self._lock = threading.Lock()
        self.state: dict = {"state": "idle"}

    def start(self, job: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.state = {"state": "running", "job": job, "started_at": _now(), "steps": []}
        threading.Thread(target=self._work, args=(job,), daemon=True).start()
        return True

    def _work(self, job: str) -> None:
        stop = threading.Event()
        if self._keepalive:
            threading.Thread(target=self._keepalive, args=(stop,), daemon=True).start()
        try:
            ok = True
            for name, cmd in job_commands(job):
                code, tail = self._runner(name, cmd)
                self.state["steps"].append({"step": name, "exit_code": code, "log_tail": tail})
                if code != 0:
                    ok = False
                    break
            self.state["state"] = "success" if ok else "failed"
        except Exception as exc:  # never let the thread die silently
            self.state["state"] = "failed"
            self.state["error"] = str(exc)[:500]
        finally:
            self.state["finished_at"] = _now()
            stop.set()
            self._lock.release()


def self_ping(stop: threading.Event) -> None:
    """Keep a free Render service awake while a job runs by calling its own public URL."""
    base = os.environ.get("RENDER_EXTERNAL_URL")
    if not base:
        return
    while not stop.wait(KEEPALIVE_EVERY_S):
        try:
            urllib.request.urlopen(base.rstrip("/") + "/health", timeout=20).read()
        except Exception as exc:
            log.warning("keep-alive ping failed: %s", exc)


def make_handler(manager: JobManager, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, query: dict) -> bool:
            given = self.headers.get("X-Run-Token") or (query.get("key") or [""])[0]
            return bool(token) and hmac.compare_digest(given.encode(), token.encode())

        def _route(self, method: str) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/health" and method == "GET":
                return self._send(200, "ok", "text/plain")
            if url.path in ("/run", "/status"):
                if not token:
                    return self._send(503, {"error": "RUN_TOKEN is not configured"})
                if not self._authorized(query):
                    return self._send(401, {"error": "unauthorized"})
                if url.path == "/status" and method == "GET":
                    return self._send(200, manager.state)
                if url.path == "/run" and method in ("POST", "GET"):
                    job = (query.get("job") or ["all"])[0]
                    if job not in ("weekly", "directors", "all"):
                        return self._send(400, {"error": "job must be weekly, directors or all"})
                    if not manager.start(job):
                        return self._send(409, {"error": "a job is already running", **manager.state})
                    return self._send(202, {"started": job, "check": "/status"})
            self._send(404, {"error": "not found"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def log_message(self, fmt, *args):  # keep the secret in ?key= out of the access log
            log.info("%s %s", self.command, urlparse(self.path).path)

    return Handler


def make_server(host: str, port: int, manager: JobManager, token: str | None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(manager, token))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("PORT", "10000"))
    token = os.environ.get("RUN_TOKEN") or None
    if not token:
        log.warning("RUN_TOKEN is not set: /run and /status are disabled")
    server = make_server("0.0.0.0", port, JobManager(keepalive=self_ping), token)
    log.info("listening on port %s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
