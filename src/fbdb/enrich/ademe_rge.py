"""ADEME RGE (Reconnu Garant de l'Environnement) certifications, matched to establishments by exact SIRET.

Source: ADEME open dataset `liste-des-entreprises-rge-2` (data.ademe.fr, also on data.gouv.fr), Licence Ouverte,
refreshed daily by ADEME, ~60k companies / ~160k certificates. Download the CSV export and pass it to this loader
(the loader is file-based like the other bulk loaders; ADEME's API/field names can change, see the aliases below).

Per matched establishment the loader REPLACES its ADEME rows in company_certifications (cert_type 'RGE'), so a
re-run is idempotent and certificates that left the list disappear for the establishments present in the file.
SIRETs that match nothing in our base go to a rejects CSV; nothing is created from this file.
Each file is recorded in certification_loads (sql/022) and its rows point to it (load_id); v_certification_status then says
found_valid / found_not_current / none_in_loaded_files for 'RGE'. An establishment that is NOT in a later file keeps its old
rows (last_seen_at shows when ADEME last listed it): decide with the client whether a row not seen in the latest full file may
still be exported.

Usage: python -m fbdb.enrich.ademe_rge FILE.csv [--rejects data/exports/rge_rejects.csv]
Needs: DATABASE_URL.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

import psycopg

log = logging.getLogger("fbdb.rge")

ALIASES = {
    "siret": ["siret"],
    "label": ["nom_qualification", "qualification", "nom_certificat", "code_qualification"],
    "domain": ["domaine", "meta_domaine"],
    "body": ["organisme", "nom_organisme"],
    "valid_from": ["date_debut", "date_debut_validite", "date_de_debut"],
    "valid_to": ["date_fin", "date_fin_validite", "date_de_fin"],
    # link to the certificate / qualification page, when the file has one. NOT verified against the live file: check the header.
    "url": ["url_qualification", "lien_certificat", "url_certificat", "lien_qualification"],
}
URL_OK = re.compile(r"^https?://\S+$", re.I)


def _key(h: str) -> str:
    h = unicodedata.normalize("NFKD", h or "").encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def parse_date(v: str) -> Optional[date]:
    v = (v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def read_rows(path: str | Path) -> Iterator[dict]:
    """Yield normalized rows: siret (14 digits or ''), label, valid_from, valid_to. Delimiter auto-detected."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = f.readline()
        f.seek(0)
        delim = ";" if head.count(";") > head.count(",") else ","
        rd = csv.DictReader(f, delimiter=delim)
        cols = {_key(c): c for c in (rd.fieldnames or [])}
        pick = {k: next((cols[a] for a in al if a in cols), None) for k, al in ALIASES.items()}
        if not pick["siret"]:
            raise ValueError(f"no SIRET column found; headers: {list(cols)}")
        for r in rd:
            g = lambda k: (r.get(pick[k]) or "").strip() if pick[k] else ""  # noqa: E731
            siret = re.sub(r"\D", "", g("siret"))
            label = g("label")
            if g("domain") and g("domain").lower() not in label.lower():
                label = f"{label} ({g('domain')})" if label else g("domain")
            yield {"siret": siret if len(siret) == 14 else "", "raw_siret": g("siret"), "label": label or None,
                   "valid_from": parse_date(g("valid_from")), "valid_to": parse_date(g("valid_to")),
                   "url": g("url") if URL_OK.match(g("url")) and len(g("url")) <= 2000 else None}


def run(dsn: str, path: str, rejects_path: Optional[str] = None) -> dict:
    stats = {"rows": 0, "matched_rows": 0, "establishments": 0, "rejected": 0}
    rows = list(read_rows(path))
    stats["rows"] = len(rows)
    with psycopg.connect(dsn) as conn:
        src = conn.execute("SELECT source_id FROM data_sources WHERE code='ademe_rge'").fetchone()[0]
        load_id = conn.execute(
            "INSERT INTO certification_loads (provider, cert_types, file_name, coverage_note) VALUES ('ademe_rge', ARRAY['RGE'], %s, %s) "
            "RETURNING load_id", (Path(path).name, "ADEME open list of RGE companies (Licence Ouverte)")).fetchone()[0]
        conn.execute("CREATE TEMP TABLE tmp_rge (siret CHAR(14), label TEXT, valid_from DATE, valid_to DATE, url TEXT) ON COMMIT DROP")
        with conn.cursor().copy("COPY tmp_rge (siret,label,valid_from,valid_to,url) FROM STDIN") as cp:
            for r in rows:
                if r["siret"]:
                    cp.write_row((r["siret"], r["label"], r["valid_from"], r["valid_to"], r["url"]))
        conn.execute("""DELETE FROM company_certifications WHERE source_id=%s AND cert_type='RGE'
                        AND establishment_id IN (SELECT a.establishment_id FROM company_addresses a JOIN tmp_rge t ON t.siret=a.siret)""", (src,))
        cur = conn.execute("""INSERT INTO company_certifications(company_id, establishment_id, cert_type, cert_label, valid_from, valid_to, source_id, load_id, certificate_url)
                              SELECT a.company_id, a.establishment_id, 'RGE', t.label, t.valid_from, t.valid_to, %s, %s, t.url
                              FROM tmp_rge t JOIN company_addresses a ON a.siret = t.siret""", (src, load_id))
        stats["matched_rows"] = cur.rowcount
        stats["establishments"] = conn.execute(
            "SELECT count(DISTINCT a.establishment_id) FROM tmp_rge t JOIN company_addresses a ON a.siret=t.siret").fetchone()[0]
        unmatched = {s for (s,) in conn.execute(
            "SELECT DISTINCT t.siret FROM tmp_rge t LEFT JOIN company_addresses a ON a.siret=t.siret WHERE a.siret IS NULL")}
        conn.execute("UPDATE certification_loads SET rows_read=%s, rows_added=%s WHERE load_id=%s",
                     (len(rows), stats["matched_rows"], load_id))
        conn.commit()
    bad = [r for r in rows if not r["siret"] or r["siret"] in unmatched]
    stats["rejected"] = len(bad)
    if rejects_path and bad:
        Path(rejects_path).parent.mkdir(parents=True, exist_ok=True)
        with open(rejects_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["siret", "reason", "label"])
            for r in bad:
                w.writerow([r["raw_siret"], "INVALID_SIRET" if not r["siret"] else "SIRET_NOT_IN_BASE", r["label"] or ""])
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--rejects", default="data/exports/rge_rejects.csv")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(os.environ["DATABASE_URL"], a.file, a.rejects))


if __name__ == "__main__":
    main()
