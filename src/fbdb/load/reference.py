"""Load official reference tables from CSV files published by INSEE.

    python -m fbdb.load.reference --naf naf_rev2.csv --legal-forms categories_juridiques.csv

Why a loader instead of hard-coded rows: NAF rev.2 has ~730 sub-classes and the legal-form nomenclature
several hundred codes. Typing them from memory risks wrong labels, which is worse than the raw code. The
files below come from INSEE; any CSV with a code column and a label column works (comma or semicolon,
UTF-8 with or without BOM, header names matched case-insensitively):

  --naf          code | label   [| trade | category]     -> naf_reference
                 codes may be written 0111Z or 01.11Z; both are stored as 01.11Z
  --legal-forms  code | label                             -> legal_form_reference (4-digit level III codes only)

Rows that cannot be loaded are counted and the first few are logged, never silently dropped. Safe to re-run.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import psycopg

log = logging.getLogger("fbdb.reference")

_NAF_PLAIN = re.compile(r"^\d{4}[A-Z]$")
_NAF_DOTTED = re.compile(r"^\d{2}\.\d{2}[A-Z]$")
_LEGAL = re.compile(r"^\d{4}$")

CODE_HEADERS = ("code", "naf", "code naf", "code_naf", "codenaf", "sous-classe", "categorie juridique", "cj")
LABEL_HEADERS = ("label", "libelle", "libellé", "intitule", "intitulé", "libelle naf", "libelle_naf", "nom", "name")


def normalize_naf(code: Optional[str]) -> Optional[str]:
    """0111Z / 01.11Z / ' 01.11z ' -> '01.11Z'; anything else (section letters, 2-3 digit levels) -> None."""
    if code is None:
        return None
    c = re.sub(r"\s+", "", str(code)).upper()
    if _NAF_DOTTED.match(c):
        return c
    if _NAF_PLAIN.match(c):
        return f"{c[:2]}.{c[2:]}"
    return None


@dataclass
class ParseResult:
    rows: list[dict] = field(default_factory=list)
    skipped: int = 0
    examples: list[str] = field(default_factory=list)

    def skip(self, why: str) -> None:
        self.skipped += 1
        if len(self.examples) < 5:
            self.examples.append(why)


def _sniff_delimiter(first_line: str) -> str:
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _read_table(path: Path, delimiter: Optional[str], encoding: str) -> Iterator[dict]:
    with open(path, newline="", encoding=encoding) as fh:
        first = fh.readline()
        fh.seek(0)
        reader = csv.reader(fh, delimiter=delimiter or _sniff_delimiter(first))
        header = [h.strip().lower() for h in next(reader, [])]
        code_i = next((i for i, h in enumerate(header) if h in CODE_HEADERS), 0)
        label_i = next((i for i, h in enumerate(header) if h in LABEL_HEADERS), 1)
        trade_i = header.index("trade") if "trade" in header else None
        cat_i = header.index("category") if "category" in header else None
        for r in reader:
            if not r or all(not c.strip() for c in r):
                continue
            get = lambda i: r[i].strip() if i is not None and i < len(r) else ""  # noqa: E731
            yield {"code": get(code_i), "label": get(label_i), "trade": get(trade_i), "category": get(cat_i)}


def parse_naf(path: Path, delimiter: Optional[str] = None, encoding: str = "utf-8-sig") -> ParseResult:
    out = ParseResult()
    seen: set[str] = set()
    for r in _read_table(path, delimiter, encoding):
        code = normalize_naf(r["code"])
        if code is None:
            out.skip(f"not a NAF sub-class code: {r['code']!r}")   # sections / divisions / classes are expected here
        elif not r["label"]:
            out.skip(f"{code}: empty label")
        elif code in seen:
            out.skip(f"{code}: duplicate code")
        else:
            seen.add(code)
            out.rows.append({"code": code, "label": r["label"], "trade": r["trade"] or None, "category": r["category"] or None})
    return out


def parse_legal_forms(path: Path, delimiter: Optional[str] = None, encoding: str = "utf-8-sig") -> ParseResult:
    out = ParseResult()
    seen: set[str] = set()
    for r in _read_table(path, delimiter, encoding):
        code = re.sub(r"\s+", "", r["code"])
        if not _LEGAL.match(code):
            out.skip(f"not a 4-digit legal form code: {r['code']!r}")   # levels I and II are expected here
        elif not r["label"]:
            out.skip(f"{code}: empty label")
        elif code in seen:
            out.skip(f"{code}: duplicate code")
        else:
            seen.add(code)
            out.rows.append({"code": code, "label": r["label"]})
    return out


UPSERT_NAF = """
INSERT INTO naf_reference (naf, label, trade, category) VALUES (%(code)s, %(label)s, %(trade)s, %(category)s)
ON CONFLICT (naf) DO UPDATE SET label = EXCLUDED.label,
  trade = COALESCE(EXCLUDED.trade, naf_reference.trade),          -- a curated trade is never wiped by a plain INSEE file
  category = COALESCE(EXCLUDED.category, naf_reference.category)
"""
UPSERT_LEGAL = """
INSERT INTO legal_form_reference (code, label) VALUES (%(code)s, %(label)s)
ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label
"""


def _report(name: str, res: ParseResult) -> None:
    log.info("%s: %d rows to load, %d skipped", name, len(res.rows), res.skipped)
    for e in res.examples:
        log.info("%s skipped (example): %s", name, e)


def load(dsn: str, naf: Optional[Path] = None, legal_forms: Optional[Path] = None,
         delimiter: Optional[str] = None, encoding: str = "utf-8-sig") -> dict:
    result: dict = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if naf:
            res = parse_naf(naf, delimiter, encoding)
            _report("naf_reference", res)
            if not res.rows:
                raise SystemExit(f"{naf}: no valid NAF code found; check the file and --delimiter/--encoding")
            cur.executemany(UPSERT_NAF, res.rows)
            result["naf_loaded"], result["naf_skipped"] = len(res.rows), res.skipped
        if legal_forms:
            res = parse_legal_forms(legal_forms, delimiter, encoding)
            _report("legal_form_reference", res)
            if not res.rows:
                raise SystemExit(f"{legal_forms}: no valid legal form code found; check the file and --delimiter/--encoding")
            cur.executemany(UPSERT_LEGAL, res.rows)
            result["legal_forms_loaded"], result["legal_forms_skipped"] = len(res.rows), res.skipped
        conn.commit()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--naf", type=Path, help="CSV with NAF rev.2 code and label")
    ap.add_argument("--legal-forms", type=Path, help="CSV with legal form code and label")
    ap.add_argument("--delimiter", help="force the CSV delimiter (default: detected)")
    ap.add_argument("--encoding", default="utf-8-sig", help="default utf-8-sig (UTF-8, BOM tolerated)")
    args = ap.parse_args()
    if not (args.naf or args.legal_forms):
        ap.error("give --naf and/or --legal-forms")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(load(os.environ["DATABASE_URL"], args.naf, args.legal_forms, args.delimiter, args.encoding))


if __name__ == "__main__":
    main()
