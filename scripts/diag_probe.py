import sys, urllib.request
out = []
def p(*a): out.append(" ".join(str(x) for x in a)); print(*a)
B = "https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2"
for u in (B, B + "/raw", B + "/full", B + "/lines?format=csv&size=10000", B + "/lines?size=1&format=json"):
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "fbdb-diag"})
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read(700).decode("utf-8", "replace").replace("\n", " | ")
            p("OK", r.status, u, "| len:", r.headers.get("Content-Length"), "| type:", r.headers.get("Content-Type"), "| link:", (r.headers.get("Link") or "")[:200])
            p("   ", body[:600])
    except Exception as e:
        p("ERR", u, e)
open(sys.argv[1], "w").write("\n".join(out))
