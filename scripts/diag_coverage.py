import os, re, sys, psycopg
sys.path.insert(0, "src")
from fbdb.api import DETAIL_SECTIONS
out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a, flush=True)
VIS = "SELECT c.company_id FROM companies c WHERE c.status='active' AND c.is_diffusible AND NOT c.is_sole_proprietor AND EXISTS (SELECT 1 FROM company_addresses a WHERE a.company_id=c.company_id AND a.status='active' AND a.is_diffusible)"
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("SET statement_timeout=0")
    total = c.execute(f"SELECT count(*) FROM ({VIS}) v").fetchone()[0]
    p("visible companies:", total)
    for name, sql in DETAIL_SECTIONS.items():
        m = re.search(r"FROM\s+(\w+)", sql)
        try:
            n = c.execute(f"SELECT count(DISTINCT t.company_id) FROM {m.group(1)} t WHERE t.company_id IN ({VIS})").fetchone()[0]
            p(f"section {name:<12} table {m.group(1):<28} companies with data: {n} ({100*n/total:.1f}%)")
        except Exception as e:
            p(f"section {name:<12} ERR {str(e)[:90]}")
    for t in ("website", "phone", "email"):
        try:
            n = c.execute(f"SELECT count(DISTINCT k.company_id) FROM company_contacts k WHERE k.contact_type=%s AND k.company_id IN ({VIS})", (t,)).fetchone()[0]
            p(f"contact {t:<8} companies: {n}")
        except Exception as e: p("contact", t, "ERR", str(e)[:80])
    p("companies with more than 1 establishment in DB:", c.execute(f"SELECT count(*) FROM (SELECT company_id FROM company_addresses WHERE company_id IN ({VIS}) GROUP BY 1 HAVING count(*)>1) x").fetchone()[0])
    p("pappers financial companies:", c.execute("select count(distinct company_id) from company_financials f join data_sources s using(source_id) where s.code='pappers'").fetchone()[0],
      "| annuaire financial companies:", c.execute("select count(distinct company_id) from company_financials f join data_sources s using(source_id) where s.code='annuaire'").fetchone()[0])
open(sys.argv[1], "w").write("\n".join(out))
