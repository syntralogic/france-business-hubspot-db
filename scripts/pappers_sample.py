"""Print only the STRUCTURE of one Pappers response (keys, types, short scalar values at top level). Never prints the URL or the key."""
import json, os, sys, urllib.request
siren = sys.argv[2]
url = f"https://api.pappers.fr/v2/entreprise?api_token={os.environ['PAPPERS_API_KEY']}&siren={siren}"
with urllib.request.urlopen(url, timeout=60) as r:
    d = json.load(r)
out = []
def short(v): return (str(v)[:70] + "…") if len(str(v)) > 70 else str(v)
for k, v in d.items():
    if isinstance(v, list):
        first = v[0] if v else None
        out.append(f"{k}: list[{len(v)}]" + (f" keys={sorted(first.keys())[:40]}" if isinstance(first, dict) else f" first={short(first)}"))
    elif isinstance(v, dict):
        out.append(f"{k}: dict keys={sorted(v.keys())[:30]}")
    else:
        out.append(f"{k}: {short(v)}")
open(sys.argv[1], "w").write("\n".join(out))
print(len(out), "top-level keys")
