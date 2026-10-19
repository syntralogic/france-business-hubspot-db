"""Precision of the people read from official sites (client spec, section 5) on a hand-labelled sample.

`fbdb.enrich.site_extract.extract_people` is heuristic (a name next to a function word, or a legal-notice formula). Nobody should
trust the people, the `function_category` or the phone / email attached to a person before this has been measured on real sites.
File based, no database and no network, so a rule change can be re-scored on the SAME pages without paying for a scrape again.

1. Save the pages of ~50 sites proven by SIREN while the paid run happens:
       python -m fbdb.enrich.website --limit 50 --save-pages data/pages          # -> data/pages/<siren>__NN.md
2. ``sample``: run the extractor on those pages (random sample of sites, every person found on them) and write a labelling sheet:
       python -m fbdb.report.people_eval sample data/pages --out to_label.csv --sites 50
3. A person fills the columns (one row = one person the extractor returned):
       person       yes  = a real person who works for THIS company (owner, manager, employee) | no = anything else (heading, town, brand,
                    customer, partner, web agency, article author, another company's staff...)
       function_ok  yes | no | na   (na when person = no, or the page gives no function)
       category_ok  yes | no | na   (our `function_category`: sales, director, technical... right for the function written)
       contacts_ok  yes | no | na   (the phone / email attached to the person really are theirs; na when none attached)
   Rows to add by hand for RECALL: copy any row, set `extra` = missed and fill the person's names (a person visible on the page that the
   extractor did not return). Recall is only indicative: it counts the people the labeller bothered to add.
4. ``score``:
       python -m fbdb.report.people_eval score labelled.csv --out reports/people_eval.json
   Reports person precision overall and per rule (legal_notice / name_and_function), function / category / contacts precision and whether
   the sample has the 50 sites asked for (``sample`` prints how many sampled sites returned no person at all). No acceptance threshold is invented here: the client
   decides what precision it accepts for each use (e.g. contact attachment needs a much higher precision than a name list).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from fbdb.enrich.interlocutors import classify_function
from fbdb.enrich.site_extract import extract_people

MIN_SITES = 50
COLUMNS = ["siren", "page_file", "page_url", "first_name", "last_name", "function", "predicted_category", "rule", "line", "phones", "emails",
           "person", "function_ok", "category_ok", "contacts_ok", "extra"]
YES_NO = {"yes", "no"}
_SOURCE = re.compile(r"^<!--\s*source:\s*(\S+)\s*-->")


def read_pages(directory: str) -> dict[str, list[tuple[str, str, str]]]:
    """{siren: [(file name, page url, markdown)]} from `<siren>__NN.md` files."""
    out: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path in sorted(Path(directory).glob("*__*.md")):
        siren = path.name.split("__", 1)[0]
        text = path.read_text(encoding="utf-8")
        m = _SOURCE.match(text)
        out[siren].append((path.name, m.group(1) if m else "", text[m.end():].lstrip("\n") if m else text))
    return dict(out)


def sample_sites(pages: dict, n: int, seed: int = 42) -> list[str]:
    sirens = sorted(pages)
    random.Random(seed).shuffle(sirens)
    return sorted(sirens[:n])


def rows_for(pages: dict, sirens: Iterable[str]) -> tuple[list[dict], dict]:
    """One row per person returned by the extractor (same person on several pages: one row per page it was read on)."""
    rows, per_site = [], {}
    for siren in sirens:
        found = 0
        for fname, url, md in pages[siren]:
            for p in extract_people(md):
                found += 1
                rows.append({"siren": siren, "page_file": fname, "page_url": url, "first_name": p["first_name"], "last_name": p["last_name"],
                             "function": p.get("function") or "", "predicted_category": classify_function(p.get("function")), "rule": p["rule"],
                             "line": p["line"], "phones": ";".join(p["phones"]), "emails": ";".join(p["emails"]),
                             "person": "", "function_ok": "", "category_ok": "", "contacts_ok": "", "extra": ""})
        per_site[siren] = found
    return rows, per_site


def _ratio(yes: int, no: int):
    return yes / (yes + no) if (yes + no) else None


def _val(row: dict, key: str) -> str:
    return (row.get(key) or "").strip().lower()


def score(rows: Iterable[dict]) -> dict:
    """Precision from labelled rows. Rows with `extra` = missed only feed recall; unlabelled rows are counted and skipped."""
    rows = list(rows)
    extracted = [r for r in rows if _val(r, "extra") != "missed"]
    missed = [r for r in rows if _val(r, "extra") == "missed"]
    labelled = [r for r in extracted if _val(r, "person") in YES_NO]
    by_rule: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    yes = no = 0
    for r in labelled:
        ok = _val(r, "person") == "yes"
        yes, no = yes + ok, no + (not ok)
        by_rule[r.get("rule") or "?"][0 if ok else 1] += 1
    real = [r for r in labelled if _val(r, "person") == "yes"]

    def sub(key: str):
        a = sum(1 for r in real if _val(r, key) == "yes")
        b = sum(1 for r in real if _val(r, key) == "no")
        return {"yes": a, "no": b, "precision": _ratio(a, b)}

    sites = {r["siren"] for r in rows if r.get("siren")}
    return {
        "sites_in_sheet": len(sites),
        "meets_sample_size": len(sites) >= MIN_SITES,
        "rows": len(extracted), "labelled": len(labelled), "unlabelled_skipped": len(extracted) - len(labelled),
        "person_precision": _ratio(yes, no),
        "person_precision_by_rule": {k: {"yes": v[0], "no": v[1], "precision": _ratio(v[0], v[1])} for k, v in sorted(by_rule.items())},
        "function_precision": sub("function_ok"), "category_precision": sub("category_ok"), "contacts_precision": sub("contacts_ok"),
        "missed_people_added_by_hand": len(missed),
        "recall_indicative": (yes / (yes + len(missed))) if (yes + len(missed)) else None,
        "note": "no acceptance threshold is set here: the client decides it per use (a contact attached to a person needs far more than a name list)",
    }


def _read(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("pages_dir")
    s.add_argument("--out", required=True)
    s.add_argument("--sites", type=int, default=MIN_SITES)
    s.add_argument("--seed", type=int, default=42)
    c = sub.add_parser("score")
    c.add_argument("labelled")
    c.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "sample":
        pages = read_pages(a.pages_dir)
        sirens = sample_sites(pages, a.sites, a.seed)
        rows, per_site = rows_for(pages, sirens)
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        empty = sum(1 for v in per_site.values() if v == 0)
        print(f"{len(sirens)} sites sampled (of {len(pages)}), {len(rows)} people to label in {a.out}; {empty} site(s) with no person found")
        if len(sirens) < MIN_SITES:
            print(f"warning: fewer than {MIN_SITES} sites: the measure is indicative only")
        return 0

    result = score(_read(a.labelled))
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
