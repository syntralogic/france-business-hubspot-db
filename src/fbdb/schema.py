"""Create or upgrade the database schema from the web service (same logic as scripts/init_db.sh, no psql needed).

Used on Render free tier, where nobody can run scripts against the database by hand. Idempotent: safe at every start.
"""
from __future__ import annotations

import logging
import os

import psycopg

log = logging.getLogger("fbdb.schema")
SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sql")
CORE = "001_schema.sql"


def sql_files(existing_schema: bool, names: list[str]) -> list[str]:
    """Fresh database: every file in order. Existing one: everything except 001 (core tables are never re-run)."""
    files = sorted(n for n in names if n.endswith(".sql"))
    return [n for n in files if n != CORE] if existing_schema else files


def ensure_schema(dsn: str, sql_dir: str = SQL_DIR) -> list[str]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        existing = conn.execute("SELECT to_regclass('public.companies') IS NOT NULL").fetchone()[0]
        applied = []
        for name in sql_files(existing, os.listdir(sql_dir)):
            with open(os.path.join(sql_dir, name), encoding="utf-8") as fh:
                conn.execute(fh.read())
            applied.append(name)
            log.info("schema: applied %s", name)
    return applied


def ensure_schema_safe(dsn: str | None) -> None:
    """Never let a schema problem stop the web service: log it and carry on."""
    if not dsn:
        return
    try:
        ensure_schema(dsn)
        log.info("schema ready")
    except Exception as exc:
        log.error("schema init failed: %s", str(exc)[:500])
        return
    if os.environ.get("AUTO_DEMO_LOAD", "0") == "1":   # opt-in demo slice (default OFF: real data comes from the full load)
        try:
            from fbdb.load.demo_load import demo_load, is_empty
            if is_empty(dsn):
                log.info("database is empty: loading a small demo slice of SIRENE (AUTO_DEMO_LOAD=1 enabled)")
                demo_load(dsn, int(os.environ.get("DEMO_ROWS", "20000")))
                log.info("demo load done")
        except Exception as exc:
            log.error("demo load failed: %s", str(exc)[:500])
    ensure_rge_demo_safe(dsn)


def ensure_rge_demo_safe(dsn: str | None) -> None:
    """Free RGE demo data (real artisans with certificates) when the database has none yet."""
    if not dsn or os.environ.get("AUTO_RGE_DEMO", "0") != "1":
        return
    try:
        from fbdb.load.rge_demo import has_rge, rge_demo
        if not has_rge(dsn):
            log.info("no RGE data yet: loading RGE companies (ADEME + Sirene API), AUTO_RGE_DEMO=1 enabled")
            rge_demo(dsn, int(os.environ.get("RGE_DEMO_COMPANIES", "3000")))
    except Exception as exc:
        log.error("rge demo failed: %s", str(exc)[:500])
