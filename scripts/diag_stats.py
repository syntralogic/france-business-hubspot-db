import sys, time, os
import psycopg
sys.path.insert(0, "src")
from fbdb.api import FROM, STATS_SQL
out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a, flush=True)
def timed(c, label, sql):
    t = time.time()
    try:
        r = c.execute(sql).fetchall()
        p(label, "OK", round(time.time() - t, 1), "s ->", r[:3])
    except Exception as e:
        p(label, "ERR", round(time.time() - t, 1), "s", repr(e)[:200])
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    c.execute("SET statement_timeout=600000")
    timed(c, "NEW cheap stats", STATS_SQL)
    timed(c, "first page of 25", f"SELECT v.siret {FROM} ORDER BY v.legal_name, v.siret LIMIT 25 OFFSET 0")
    timed(c, "cheap: establishments", "SELECT count(*) FROM company_addresses")
    timed(c, "cheap: rge establishments", "SELECT count(DISTINCT establishment_id) FROM company_certifications WHERE cert_type='RGE'")
    timed(c, "db size", "SELECT pg_size_pretty(pg_database_size(current_database()))")
open(sys.argv[1], "w").write("\n".join(out))
