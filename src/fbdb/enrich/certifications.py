"""Trade certifications and insurance from a provider/licence FILE: Qualibat, Qualifelec, Qualit'EnR, RC pro, decennale.

There is no open API for these (RGE is the exception: enrich/ademe_rge.py). Qualibat, Qualifelec and Qualit'EnR publish
directories but their terms do not allow bulk copying, so this loader is PROVIDER-AGNOSTIC like licensed_contacts: the data
comes as a CSV under a signed licence (or the client's own extract) and is matched EXACTLY on SIRET (establishment) or SIREN
(company). Refuses to run without a contract reference (--contract-ref or CERTIFICATIONS_CONTRACT_REF), written to the batch notes.

CSV columns (mapping overridable): siret, siren, cert_type, label, valid_from, valid_to (ISO dates or DD/MM/YYYY).
cert_type is normalised to: Qualibat, Qualifelec, Qualit'EnR, RC pro, Decennale. Anything else is rejected, never guessed.
Only active, diffusible companies; existing rows are never deleted (a partial file removes nothing); rejects go to a CSV.

Every file is recorded in certification_loads (provider, contract, types it carried, counts) and each inserted row points to it
(load_id). A row already known whose valid_to differs is NOT overwritten: the first value stays and the other goes to
field_conflicts (`certification:<type>:<label>:<valid_from>:valid_to`). "Not in the file" is never stored as "not certified":
view v_certification_status says found_valid / found_not_current / none_in_loaded_files (a type no file carried = not checked).

Usage: python -m fbdb.enrich.certifications FILE.csv --contract-ref ABC-2026-01 [--provider NAME] [--coverage-note TEXT] [--mapping map.json]
Needs DATABASE_URL.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import psycopg

from fbdb.common.provenance import record_conflict
from fbdb.transform.normalize import is_valid_siren, is_valid_siret

log = logging.getLogger("fbdb.certifications")
DEFAULT_MAPPING = {"siret": "siret", "siren": "siren", "cert_type": "cert_type", "label": "label",
                   "valid_from": "valid_from", "valid_to": "valid_to"}
CERT_TYPES = {"qualibat": "Qualibat", "qualifelec": "Qualifelec", "qualitenr": "Qualit'EnR", "rcpro": "RC pro",
              "decennale": "Decennale", "assurancedecennale": "Decennale", "rcprofessionnelle": "RC pro"}
SOURCE_CODE = {"Qualibat": "qualibat", "Qualifelec": "qualifelec", "Qualit'EnR": "qualitenr"}   # others: certification_file
GENERIC_SOURCE = "certification_file"
REJECT_FIELDS = ["line", "siret", "siren", "code", "detail"]


class LicenceError(RuntimeError):
    pass


def normalize_cert_type(raw: str) -> Optional[str]:
    s = unicodedata.normalize("NFKD", raw or "").encode("ascii", "ignore").decode().lower()
    return CERT_TYPES.get("".join(ch for ch in s if ch.isalnum()))


def parse_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def parse_row(row: dict, mapping: dict) -> tuple[Optional[str], Optional[str], Optional[dict], Optional[tuple[str, str]]]:
    """(siret, siren, cert or None, (reject_code, detail) or None). Pure function."""
    get = lambda k: (row.get(mapping.get(k) or "") or "").strip()
    siret, siren = "".join(c for c in get("siret") if c.isdigit()), "".join(c for c in get("siren") if c.isdigit())
    if siret and not is_valid_siret(siret):
        return None, None, None, ("INVALID_SIRET", siret)
    if not siret:
        if not is_valid_siren(siren):
            return None, None, None, ("NO_VALID_ID", "need a valid SIRET or SIREN")
    else:
        siren = siret[:9]
    ctype = normalize_cert_type(get("cert_type"))
    if not ctype:
        return None, None, None, ("UNKNOWN_CERT_TYPE", get("cert_type"))
    vf, vt = parse_date(get("valid_from")), parse_date(get("valid_to"))
    if get("valid_to") and vt is None:
        return None, None, None, ("BAD_DATE", get("valid_to"))
    if vf and vt and vt < vf:
        return None, None, None, ("BAD_DATE", f"{vf} > {vt}")
    return (siret or None), siren, {"type": ctype, "label": get("label") or None, "valid_from": vf, "valid_to": vt}, None


def load(dsn: str, path: str, contract_ref: Optional[str], mapping: Optional[dict] = None, rejects_path: Optional[str] = None,
         provider: str = "licensed_file", coverage_note: Optional[str] = None) -> dict:
    ref = contract_ref or os.environ.get("CERTIFICATIONS_CONTRACT_REF")
    if not ref:
        raise LicenceError("Refusing to load certifications without a contract/licence reference (--contract-ref)")
    mapping = {**DEFAULT_MAPPING, **(mapping or {})}
    stats = {"rows": 0, "inserted": 0, "already_known": 0, "not_in_base": 0, "rejected": 0, "conflicts": 0}
    seen_types: set[str] = set()
    rejects_path = rejects_path or str(Path(path).with_suffix(".rejects.csv"))
    with psycopg.connect(dsn) as conn, open(path, newline="", encoding="utf-8-sig") as fh, \
            open(rejects_path, "w", newline="", encoding="utf-8") as rj:
        rejects = csv.DictWriter(rj, fieldnames=REJECT_FIELDS)
        rejects.writeheader()
        src = {code: sid for code, sid in conn.execute("SELECT code, source_id FROM data_sources")}
        batch_id = conn.execute(
            "INSERT INTO import_export_batches (batch_type, notes) VALUES ('enrich', %s) RETURNING batch_id",
            (f"certifications contract_ref={ref} file={Path(path).name}",)).fetchone()[0]
        load_id = conn.execute(
            "INSERT INTO certification_loads (provider, file_name, contract_ref, coverage_note) VALUES (%s,%s,%s,%s) RETURNING load_id",
            (provider, Path(path).name, ref, coverage_note)).fetchone()[0]
        conn.commit()
        for i, row in enumerate(csv.DictReader(fh), start=2):
            stats["rows"] += 1
            siret, siren, cert, err = parse_row(row, mapping)
            if err:
                stats["rejected"] += 1
                rejects.writerow({"line": i, "siret": siret or "", "siren": siren or "", "code": err[0], "detail": err[1]})
                continue
            seen_types.add(cert["type"])
            comp = conn.execute("SELECT company_id FROM companies WHERE siren=%s AND status='active' AND is_diffusible", (siren,)).fetchone()
            if not comp:
                stats["not_in_base"] += 1
                continue
            est_id = None
            if siret:
                e = conn.execute("SELECT establishment_id FROM company_addresses WHERE siret=%s", (siret,)).fetchone()
                est_id = e[0] if e else None
            sid = src.get(SOURCE_CODE.get(cert["type"], "")) or src.get(GENERIC_SOURCE)
            exists = conn.execute(
                """SELECT certification_id, valid_to FROM company_certifications WHERE company_id=%s AND cert_type=%s
                     AND COALESCE(cert_label,'')=COALESCE(%s,'') AND valid_from IS NOT DISTINCT FROM %s""",
                (comp[0], cert["type"], cert["label"], cert["valid_from"])).fetchone()
            if exists:
                stats["already_known"] += 1
                conn.execute("UPDATE company_certifications SET last_seen_at=now() WHERE certification_id=%s", (exists[0],))
                if cert["valid_to"] != exists[1] and cert["valid_to"] is not None:
                    field = f"certification:{cert['type']}:{cert['label'] or '-'}:{cert['valid_from'] or '-'}:valid_to"
                    record_conflict(conn, comp[0], field, exists[1] or "none", sid, cert["valid_to"], sid)
                    stats["conflicts"] += 1
                continue
            conn.execute(
                """INSERT INTO company_certifications (company_id, establishment_id, cert_type, cert_label, valid_from, valid_to,
                     source_id, load_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (comp[0], est_id, cert["type"], cert["label"], cert["valid_from"], cert["valid_to"], sid, load_id))
            stats["inserted"] += 1
            if stats["rows"] % 1000 == 0:
                conn.commit()
        conn.execute(
            "UPDATE import_export_batches SET status='success', finished_at=now(), rows_read=%s, rows_added=%s, rows_unchanged=%s, "
            "rows_review=%s WHERE batch_id=%s",
            (stats["rows"], stats["inserted"], stats["already_known"], stats["rejected"], batch_id))
        conn.execute(
            """UPDATE certification_loads SET cert_types=%s, rows_read=%s, rows_added=%s, rows_unchanged=%s, rows_rejected=%s
               WHERE load_id=%s""",
            (sorted(seen_types), stats["rows"], stats["inserted"], stats["already_known"], stats["rejected"], load_id))
        conn.commit()
    stats["reject_file"] = rejects_path
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--contract-ref")
    ap.add_argument("--mapping")
    ap.add_argument("--provider", default="licensed_file", help="who supplied the file (written to certification_loads)")
    ap.add_argument("--coverage-note", help="what the file covers, e.g. 'Qualibat directory extract of 2026-10, departments 69 and 38'")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mapping = json.load(open(a.mapping, encoding="utf-8")) if a.mapping else None
    print(load(os.environ["DATABASE_URL"], a.file, a.contract_ref, mapping, provider=a.provider, coverage_note=a.coverage_note))


if __name__ == "__main__":
    main()
