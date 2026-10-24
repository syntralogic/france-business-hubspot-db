"""Download the FULL ADEME RGE list (data-fair API, 10,000 rows per page, follows the Link rel=next header) into one CSV."""
import re, sys, urllib.request
URL = "https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2/lines?format=csv&size=10000"
out = sys.argv[1]
n_pages = n_lines = 0
with open(out, "wb") as f:
    url = URL
    while url:
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "fbdb"}), timeout=180) as r:
                    body = r.read(); link = r.headers.get("Link") or ""
                break
            except Exception as e:
                print("retry", attempt, e)
        else:
            raise SystemExit("download failed: " + url)
        if n_pages:
            body = body.split(b"\n", 1)[1] if b"\n" in body else b""
        f.write(body); n_pages += 1; n_lines += body.count(b"\n")
        m = re.search(r'<([^>]+)>;\s*rel=next', link)
        url = m.group(1) if m and body.strip() else None
        print("page", n_pages, "lines so far", n_lines, flush=True)
