import json
import threading
import time
import urllib.error
import urllib.request

from fbdb import web


def call(base, path, method="GET", token=None):
    req = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    if token:
        req.add_header("X-Run-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def serve(runner, token="s3cret"):
    server = web.make_server("127.0.0.1", 0, web.JobManager(runner=runner), token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def wait_done(base, token="s3cret"):
    for _ in range(100):
        state = json.loads(call(base, "/status", token=token)[1])
        if state["state"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health_is_public_and_run_needs_token():
    server, base = serve(lambda n, c: (0, ""))
    try:
        assert call(base, "/health") == (200, "ok")
        assert call(base, "/run", "POST")[0] == 401
        assert call(base, "/run", "POST", token="wrong")[0] == 401
        assert call(base, "/status")[0] == 401
    finally:
        server.shutdown()


def test_run_disabled_without_token():
    server, base = serve(lambda n, c: (0, ""), token=None)
    try:
        assert call(base, "/run", "POST", token="anything")[0] == 503
        assert call(base, "/health")[0] == 200
    finally:
        server.shutdown()


def test_all_runs_the_repo_weekly_script_and_reports_status():
    seen = []
    server, base = serve(lambda name, cmd: (seen.append((name, cmd)), (0, f"{name} ok"))[1])
    try:
        assert call(base, "/run?job=all", "POST", token="s3cret")[0] == 202
        state = wait_done(base)
        assert state["state"] == "success" and [n for n, _ in seen] == ["all"]
        assert seen[0][1][0] == "bash" and seen[0][1][1].endswith("scripts/run_weekly.sh")
        assert [s["exit_code"] for s in state["steps"]] == [0]
    finally:
        server.shutdown()


def test_nonzero_exit_marks_run_failed_and_keeps_log_tail():
    server, base = serve(lambda name, cmd: (1, "boom"))
    try:
        call(base, "/run", "POST", token="s3cret")
        state = wait_done(base)
        assert state["state"] == "failed" and state["steps"][0]["log_tail"] == "boom"
    finally:
        server.shutdown()


def test_second_run_while_running_gets_409_and_bad_job_400():
    gate = threading.Event()
    server, base = serve(lambda n, c: (gate.wait(5), (0, ""))[1])
    try:
        assert call(base, "/run?job=weekly", "POST", token="s3cret")[0] == 202
        assert call(base, "/run?job=weekly", "POST", token="s3cret")[0] == 409
        assert call(base, "/run?job=nope", "POST", token="s3cret")[0] == 400
        gate.set()
        assert wait_done(base)["state"] == "success"
    finally:
        gate.set()
        server.shutdown()
