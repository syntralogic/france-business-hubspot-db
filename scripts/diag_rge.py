"""Diagnostic: why are there so few RGE matches? Writes a text report (no secrets) to the path in argv[1]."""
import csv, io, json, os, sys, urllib.request
import psycopg
sys.path.insert(0, "src")
from fbdb.enrich.ademe_rge import read_rows

out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a)

def get(url, n=None):
    req = urllib.request.Request(url, headers={"User-Agent": "fbdb-diag"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read() if n is None else r.read(n)

for slug in ("liste-des-entreprises-rge", "liste-des-entreprises-rge-1", "liste-des-entreprises-rge-2"):
    try:
        d = json.loads(get(f"https://www.data.gouv.fr/api/1/datasets/{slug}/"))
        p("DATASET", slug, "|", d.get("title"), "| org:", (d.get("organization") or {}).get("name"), "| modified:", d.get("last_modified"))
        for r in d.get("resources", [])[:12]:
            p("  RES", r.get("format"), r.get("filesize"), r.get("last_modified"), r.get("title"), r.get("url"))
    except Exception as e:
        p("DATASET", slug, "ERR", e)

url = os.environ.get("RGE_URL", "")
p("USING", url)
path = "/tmp/rge.csv"
try:
    open(path, "wb").write(get(url))
    p("file bytes:", os.path.getsize(path))
    head = open(path, encoding="utf-8-sig", errors="replace").read(600)
    p("HEAD:", head.replace("\n", " | ")[:600])
    rows = list(read_rows(path))
    sirets = {r["siret"] for r in rows if r["siret"]}
    p("rows:", len(rows), "valid siret rows:", sum(1 for r in rows if r["siret"]), "distinct siret:", len(sirets))
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        c.execute("SET statement_timeout=0")
        c.execute("CREATE TEMP TABLE t(siret char(14))")
        with c.cursor().copy("COPY t FROM STDIN") as cp:
            for s in sirets: cp.write_row((s,))
        p("establishments in DB:", c.execute("select count(*) from company_addresses").fetchone()[0])
        p("file SIRET matching DB (exact):", c.execute("select count(distinct a.siret) from t join company_addresses a on a.siret=t.siret").fetchone()[0])
        p("file SIREN present in DB (any establishment):", c.execute("select count(distinct left(t.siret,9)) from t join company_addresses a on left(a.siret,9)=left(t.siret,9)").fetchone()[0])
        p("file SIRET in scope depts by prefix:", c.execute("select count(*) from t where left(siret,9) in (select left(siret,9) from company_addresses)").fetchone()[0])
        p("RGE establishments stored now:", c.execute("select count(distinct establishment_id) from company_certifications where cert_type='RGE'").fetchone()[0])
except Exception as e:
    p("ERR", repr(e))
open(sys.argv[1], "w").write("\n".join(out))
