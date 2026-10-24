import csv, sys, collections
csv.field_size_limit(10**8)
out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a, flush=True)
def scan(path, state_col, naf_col, zip_col=None):
    tot = act = tn = tna = 0
    bydept = collections.Counter()
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f); h = next(r)
        si, ni = h.index(state_col), h.index(naf_col)
        zi = h.index(zip_col) if zip_col else None
        for row in r:
            tot += 1
            a = row[si] == "A"; act += a
            if row[ni][:2] in ("41", "42", "43"):
                tn += 1
                if a:
                    tna += 1
                    if zi is not None:
                        z = row[zi]; bydept[z[:3] if z[:2] == "97" else z[:2]] += 1
    return tot, act, tn, tna, bydept
t = scan("data/raw/StockUniteLegale_utf8.csv", "etatAdministratifUniteLegale", "activitePrincipaleUniteLegale")
p("UNITES LEGALES (companies): total", t[0], "| active", t[1], "| NAF 41/42/43 total", t[2], "| NAF 41/42/43 active", t[3])
e = scan("data/raw/StockEtablissement_utf8.csv", "etatAdministratifEtablissement", "activitePrincipaleEtablissement", "codePostalEtablissement")
p("ETABLISSEMENTS: total", e[0], "| active", e[1], "| NAF 41/42/43 total", e[2], "| NAF 41/42/43 active", e[3])
p("NAF 41/42/43 active establishments by department (top 40):")
for d, n in e[4].most_common(40): p("  ", d, n)
open(sys.argv[1], "w").write("\n".join(out))
