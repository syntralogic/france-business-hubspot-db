import json, sys, urllib.request
out = []
for siren in ("492892195", "529968497", "530047547", "509239729"):
    with urllib.request.urlopen(urllib.request.Request(f"https://recherche-entreprises.api.gouv.fr/search?q={siren}&per_page=1", headers={"User-Agent": "fbdb"}), timeout=60) as r:
        res = (json.load(r).get("results") or [None])[0]
    if not res: out.append(f"{siren}: no result"); continue
    if siren == "492892195": out.append("TOP KEYS: " + str(sorted(res.keys())))
    out.append(f"{siren}: finances={json.dumps(res.get('finances'))[:200]} | complements={json.dumps(res.get('complements'))[:300]} | dirigeants={len(res.get('dirigeants') or [])}")
open(sys.argv[1], "w").write("\n".join(out))
