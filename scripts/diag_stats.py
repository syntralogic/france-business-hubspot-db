"""Ops check: ANALYZE, then time the dashboard header query and a first page. Writes a short report (no secrets)."""
import os, sys, time
import psycopg
sys.path.insert(0, "src")
from fbdb.api import FROM, STATS_SQL

out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a, flush=True)

def timed(c, label, sql):
    t = time.time()
    try:
        rows = c.execute(sql).fetchall()
        p(label, "OK", round(time.time() - t, 1), "s ->", rows[:12])
    except Exception as e:
        p(label, "ERR", round(time.time() - t, 1), "s", repr(e)[:200])

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("SET statement_timeout=0")
    t0 = time.time(); c.execute("ANALYZE"); p("ANALYZE done in", round(time.time() - t0, 1), "s")
    timed(c, "dashboard stats", STATS_SQL)
    timed(c, "first page of 25", f"SELECT v.siret {FROM} ORDER BY v.legal_name, v.siret LIMIT 25 OFFSET 0")
    timed(c, "database size", "SELECT pg_size_pretty(pg_database_size(current_database()))")
    timed(c, "largest tables (data + indexes)", "SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size, reltuples::bigint AS rows FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND relkind = 'r' ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 12")
open(sys.argv[1], "w").write("\n".join(out))
