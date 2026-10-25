import os, sys, psycopg
out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a, flush=True)
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    p("checks by status:", c.execute("select status, count(*) from financials_checks group by 1 order by 1").fetchall())
    p("details (not_found/inaccessible):", c.execute("select status, left(coalesce(detail,''),160), count(*) from financials_checks where status<>'found' group by 1,2 order by 3 desc limit 5").fetchall())
    p("financial rows:", c.execute("select count(*), count(distinct company_id) from company_financials").fetchone())
    p("legal sheets:", c.execute("select count(*) from company_legal_info").fetchone(), "documents:", c.execute("select count(*) from company_documents").fetchone())
    for r in c.execute("select f.company_id, f.fiscal_year, f.period_end, f.duration_months, f.revenue_eur, f.net_result_eur, f.equity_eur, f.employees from company_financials f order by f.company_id desc, f.fiscal_year desc limit 8"):
        p("  row:", r)
open(sys.argv[1], "w").write("\n".join(out))
