"""Tiny HTTP wrapper so the weekly pipeline can run on a Render FREE web service.

Render free tier only offers web services (cron jobs are paid), and a web service must listen on $PORT. This module
starts a minimal server (standard library only) with three endpoints:

  GET  /health            -> 200 "ok". Public. Use it as the Render health check and for keep-alive pings.
  POST /run?job=all       -> starts the job in the background and answers 202 at once (409 if one is running).
                             job = all | weekly | directors. Default all = scripts/run_weekly.sh (the full weekly run:
                             delta, directors, geocode, BODACC, ... , job_runs record, failure alert). weekly and
                             directors run just that one step.
  GET  /status            -> JSON with the state and the log tail of the last run.
  GET  /api/stats, /api/companies  -> read-only public data API for the demo viewer (fbdb.api). No token; CORS is
                             limited to CORS_ORIGINS (default https://fbdb-viewer.vercel.app, comma separated).

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
import decimal
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from fbdb.api import DbApi
from fbdb.schema import ensure_schema_safe

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


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    raise TypeError(f"not serializable: {type(o)}")


def cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "https://fbdb-viewer.vercel.app")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def make_handler(manager: JobManager, token: str | None, api=None, origins: list[str] | None = None):
    api = api if api is not None else DbApi(os.environ.get("DATABASE_URL"))
    origins = origins if origins is not None else cors_origins()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else (
                body if isinstance(body, str) else json.dumps(body, default=_json_default)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            origin = self.headers.get("Origin", "")
            if self.path.startswith("/api/") and ("*" in origins or origin.rstrip("/") in origins):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Cache-Control", "public, max-age=30")
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
            if url.path.startswith("/api/"):
                if method != "GET":
                    return self._send(405, {"error": "read-only API"})
                try:
                    code, body = api.handle(url.path, query)
                except Exception as exc:  # DB down, timeout, bad query: never leak internals to the browser
                    log.warning("api error on %s: %s", url.path, exc)
                    code, body = 500, {"error": "query failed"}
                return self._send(code, body)
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

        def do_OPTIONS(self):  # CORS preflight
            origin = self.headers.get("Origin", "")
            self.send_response(204)
            if "*" in origins or origin.rstrip("/") in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):  # keep the secret in ?key= out of the access log
            log.info("%s %s", self.command, urlparse(self.path).path)

    return Handler


def make_server(host: str, port: int, manager: JobManager, token: str | None, api=None,
                origins: list[str] | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(manager, token, api, origins))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("PORT", "10000"))
    token = os.environ.get("RUN_TOKEN") or None
    if not token:
        log.warning("RUN_TOKEN is not set: /run and /status are disabled")
    if os.environ.get("AUTO_INIT_SCHEMA", "1") != "0":   # background: the health check must answer at once
        threading.Thread(target=ensure_schema_safe, args=(os.environ.get("DATABASE_URL"),), daemon=True).start()
    server = make_server("0.0.0.0", port, JobManager(keepalive=self_ping), token)
    log.info("listening on port %s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
