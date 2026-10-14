"""Small free demo load: the first rows of the official SIRENE stock files (data.gouv.fr, open data, no key).

The stock zips are ~1-2 GB, but a zip entry is a deflate stream we can read from the start: we download only the first
few MB, stop, and load that slice with the normal loader. Companies are kept only when active; establishments only
for companies in the slice. Meant for testing the pipeline and the viewer, NOT a representative sample of France
(the files are ordered by SIREN number).

  python -m fbdb.load.demo_load [--rows 20000]        (needs DATABASE_URL)
  Automatic at web start when the companies table is empty (AUTO_DEMO_LOAD=0 to disable, DEMO_ROWS to resize).
"""
from __future__ import annotations

import argparse
import codecs
import csv
import logging
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Iterator

import httpx
import psycopg

from fbdb.load.sirene_loader import load

log = logging.getLogger("fbdb.demo_load")
BASE = os.environ.get("SIRENE_STOCK_BASE", "https://files.data.gouv.fr/insee-sirene")
MAX_ETAB_SCAN = 600_000


def zip_first_entry_lines(chunks: Iterator[bytes]) -> Iterator[str]:
    """Decode the first entry of a (possibly truncated) zip stream, line by line, without needing the whole file."""
    buf, header_done, dec, tail = b"", False, None, ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")   # a multibyte character can span two chunks
    for chunk in chunks:
        if not header_done:
            buf += chunk
            if len(buf) < 30:
                continue
            if buf[:4] != b"PK\x03\x04":
                raise ValueError("not a zip stream")
            method, = struct.unpack("<H", buf[8:10])
            name_len, extra_len = struct.unpack("<HH", buf[26:30])
            if len(buf) < 30 + name_len + extra_len:
                continue
            if method != 8:
                raise ValueError(f"unsupported zip compression {method}")
            dec, chunk, header_done = zlib.decompressobj(-15), buf[30 + name_len + extra_len:], True
        text = tail + decoder.decode(dec.decompress(chunk))
        *lines, tail = text.split("\n")
        yield from (ln + "\n" for ln in lines)
        if dec.eof:
            break


def stream_rows(url: str) -> Iterator[list[str]]:
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        yield from csv.reader(zip_first_entry_lines(r.iter_bytes(65536)))


def build_slice(workdir: Path, rows: int) -> tuple[Path, Path]:
    ul_path, et_path = workdir / "ul.csv", workdir / "etab.csv"
    sirens: set[str] = set()
    with open(ul_path, "w", newline="", encoding="utf-8") as fh:
        w, it = csv.writer(fh), stream_rows(f"{BASE}/StockUniteLegale_utf8.zip")
        header = next(it)
        w.writerow(header)
        i_siren, i_state = header.index("siren"), header.index("etatAdministratifUniteLegale")
        n = 0
        for row in it:
            if len(row) != len(header):
                continue
            w.writerow(row)
            n += 1
            if row[i_state] == "A":
                sirens.add(row[i_siren])
            if n >= rows:
                break
    log.info("demo: %d legal units read, %d active", n, len(sirens))
    with open(et_path, "w", newline="", encoding="utf-8") as fh:
        w, it = csv.writer(fh), stream_rows(f"{BASE}/StockEtablissement_utf8.zip")
        header = next(it)
        w.writerow(header)
        i_siren = header.index("siren")
        kept = scanned = 0
        for row in it:
            scanned += 1
            if len(row) == len(header) and row[i_siren] in sirens:
                w.writerow(row)
                kept += 1
            if scanned >= MAX_ETAB_SCAN:
                break
    log.info("demo: %d establishments kept (scanned %d)", kept, scanned)
    return ul_path, et_path


def demo_load(dsn: str, rows: int = 20000) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ul, et = build_slice(Path(tmp), rows)
        load(ul, et, True, dsn)


def is_empty(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM companies").fetchone()[0] == 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=int(os.environ.get("DEMO_ROWS", "20000")))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    demo_load(os.environ["DATABASE_URL"], args.rows)


if __name__ == "__main__":
    main()
