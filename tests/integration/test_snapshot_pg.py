"""scripts/snapshot.sh against a REAL PostgreSQL: one restorable file, never a bad file, never the password in the output."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set"),
              pytest.mark.skipif(shutil.which("pg_dump") is None or shutil.which("pg_restore") is None, reason="postgresql-client missing")]
ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO companies (siren, legal_name) VALUES ('552100554','ALPHA'), ('732829320','BETA')")
    yield


def run(url, out_dir, **extra):
    env = {**os.environ, "DATABASE_URL": url, "SNAPSHOT_DIR": str(out_dir), **extra}
    return subprocess.run(["bash", str(ROOT / "scripts" / "snapshot.sh")], capture_output=True, text=True, env=env, cwd=ROOT)


def test_snapshot_is_one_restorable_file_with_the_data_and_old_ones_are_pruned(db, tmp_path):
    old = tmp_path / "fbdb_2020-01-01.dump"
    old.write_bytes(b"old")
    os.utime(old, (1, 1))                                                    # far older than the 28 days kept
    r = run(DSN, tmp_path)
    assert r.returncode == 0, r.stderr
    dumps = sorted(p.name for p in tmp_path.glob("fbdb_*.dump"))
    assert len(dumps) == 1 and not old.exists() and list(tmp_path.glob("*.tmp")) == [] and list(tmp_path.glob("*.err")) == []
    # restore it into a scratch database: the companies are back
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS fbdb_snapshot_check")
        conn.execute("CREATE DATABASE fbdb_snapshot_check")
    scratch = DSN.rsplit("/", 1)[0] + "/fbdb_snapshot_check"
    try:
        rr = subprocess.run(["pg_restore", "--no-owner", "-d", scratch, str(tmp_path / dumps[0])], capture_output=True, text=True)
        assert rr.returncode == 0, rr.stderr
        with psycopg.connect(scratch) as c2:
            assert c2.execute("SELECT count(*) FROM companies").fetchone()[0] == 2
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP DATABASE IF EXISTS fbdb_snapshot_check")


def test_failed_snapshot_leaves_no_file_and_never_prints_the_password(tmp_path):
    r = run("postgresql://u:TOPSECRETPW@127.0.0.1:1/none", tmp_path)
    assert r.returncode == 1 and "snapshot failed" in r.stderr
    assert "TOPSECRETPW" not in r.stdout + r.stderr
    assert list(tmp_path.iterdir()) == []


def test_snapshot_needs_a_database_url(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "snapshot.sh")], capture_output=True, text=True, env={**env, "SNAPSHOT_DIR": str(tmp_path)}, cwd=ROOT)
    assert r.returncode != 0 and "DATABASE_URL" in r.stderr
