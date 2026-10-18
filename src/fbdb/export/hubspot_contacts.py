"""HubSpot Contacts export (people of a company). Reads v_hubspot_contacts, writes hubspot_contacts_<date>_partNN.csv.

Headers are HubSpot property LABELS. "Company domain name" lets HubSpot associate the contact with the imported company; when a
company has no proven website the domain is empty and the contact must be associated by "Company name" (and the custom property SIREN).
Custom properties to create beforehand: SIREN, Fonction categorie, Decideur confirme, Preuve email, Preuve telephone, Preuve mobile.

Usage: python -m fbdb.export.hubspot_contacts [--out data/exports]
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import psycopg
from psycopg.rows import dict_row

from fbdb.export.hubspot import MAX_ROWS_PER_FILE
from fbdb.transform.normalize import normalize_email, normalize_phone_fr

log = logging.getLogger("fbdb.export.contacts")

# (CSV header label, source key)
COLUMNS = [
    ("First Name", "first_name"), ("Last Name", "last_name"), ("Job Title", "job_title"), ("Email", "email"),
    ("Phone Number", "phone"), ("Mobile Phone Number", "mobile"), ("Company name", "company_name"),
    ("Company domain name", "company_domain"), ("SIREN", "siren"), ("Fonction categorie", "function_category"),
    ("Decideur confirme", "decision_maker_confirmed"), ("Preuve email", "email_evidence"),
    ("Preuve telephone", "phone_evidence"), ("Preuve mobile", "mobile_evidence"),
]
HEADERS = [c[0] for c in COLUMNS]


def build_row(src: dict) -> dict:
    """One view record -> one CSV row. Values are re-validated: an invalid email/phone is blanked, never exported."""
    row = {key: ("" if src.get(key) is None else str(src[key]).strip()) for _, key in COLUMNS}
    row["email"] = normalize_email(row["email"]) or ""
    row["phone"] = normalize_phone_fr(row["phone"]) or ""
    row["mobile"] = normalize_phone_fr(row["mobile"]) or ""
    if not row["email"]:
        row["email_evidence"] = ""
    if not row["phone"]:
        row["phone_evidence"] = ""
    if not row["mobile"]:
        row["mobile_evidence"] = ""
    row["decision_maker_confirmed"] = "Oui" if src.get("decision_maker_confirmed") else "Non"
    return row


def export_contacts(records: Iterable[dict], out_dir: Path, run_date: Optional[date] = None) -> dict:
    """Write the CSV part files. A person left with no valid email/phone/mobile after re-validation is skipped and counted."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()
    files: list[Path] = []
    fh = writer = None
    in_part = part = exported = skipped = 0
    try:
        for src in records:
            row = build_row(src)
            if not (row["email"] or row["phone"] or row["mobile"]) or not row["last_name"]:
                skipped += 1
                continue
            if writer is None or in_part >= MAX_ROWS_PER_FILE:
                if fh:
                    fh.close()
                part += 1
                path = out_dir / f"hubspot_contacts_{run_date.isoformat()}_part{part:02d}.csv"
                files.append(path)
                fh = open(path, "w", newline="", encoding="utf-8")
                writer = csv.writer(fh)
                writer.writerow(HEADERS)
                in_part = 0
            writer.writerow([row[key] for _, key in COLUMNS])
            in_part += 1
            exported += 1
    finally:
        if fh:
            fh.close()
    return {"exported": exported, "skipped": skipped, "files": [str(f) for f in files]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/exports"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor(name="export_contacts_cur", row_factory=dict_row) as cur:
            cur.itersize = 20_000
            cur.execute("SELECT * FROM v_hubspot_contacts ORDER BY siren, interlocutor_id")
            result = export_contacts(cur, args.out)
    log.info("Contacts export finished: %s", result)
    print(result)


if __name__ == "__main__":
    main()
