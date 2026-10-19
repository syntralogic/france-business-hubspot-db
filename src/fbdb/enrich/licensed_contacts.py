"""Licensed phone/email/website ingestion (file feed from a contracted B2B provider).

Pages Jaunes/Solocal, Kompass, D&B, Cognism ... have no public API we may call without a contract, and scraping is
prohibited (docs/03-source-comparison.md). This module is therefore PROVIDER-AGNOSTIC: the provider delivers a CSV
under a signed licence and this loader matches it to the register and stores the values in `company_contacts`
(source `licensed_contacts`). Nothing is fetched from the web.

Guards
  * Licence gate: refuses to run without a contract reference (--contract-ref or LICENSED_CONTACTS_CONTRACT_REF);
    the reference is written to the batch notes for audit.
  * Matching is exact: SIRET -> establishment-level contact, else SIREN -> company-level contact. No fuzzy matching.
  * Only active, diffusible companies are loaded (non-diffusible units are never enriched).
  * Phones go through normalize_phone_fr (E.164, invalid -> rejected), emails through normalize_email.
    Values are never invented or inferred; status is 'sourced' (or 'verified' only if --status verified, i.e. the
    contract guarantees provider-side verification).
  * Existing values from other sources are never overridden as primary; new values are added.
  * Every inserted value is written to company_change_history; every rejected row goes to a rejects CSV.
  * A partial file never deletes anything: absent values are simply not touched.

Usage: python -m fbdb.enrich.licensed_contacts FILE.csv --contract-ref ABC-2026-01 [--mapping map.json] [--status sourced]
Mapping JSON (all optional): {"siret": "SIRET", "siren": "SIREN", "phones": ["Tel", "Mobile"], "emails": ["Mail"],
"website": "Site web", "linkedin": "LinkedIn"}. Needs DATABASE_URL.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg

from fbdb.common.provenance import record_conflict
from fbdb.match.resolve import contact_conflict
from fbdb.transform.normalize import (
    is_valid_siren, is_valid_siret, normalize_domain, normalize_email, normalize_phone_fr,
)

log = logging.getLogger("fbdb.licensed_contacts")
COMMIT_EVERY = 1000
DEFAULT_MAPPING = {
    "siret": "siret", "siren": "siren", "phones": ["phone", "phone_2"], "emails": ["email"],
    "website": "website", "linkedin": "linkedin",
}
REJECT_FIELDS = ["line", "siret", "siren", "code", "detail"]


class LicenceError(RuntimeError):
    pass


def _cell(row: dict, col: Optional[str]) -> str:
    return (row.get(col) or "").strip() if col else ""


def parse_row(row: dict, mapping: dict) -> tuple[Optional[str], Optional[str], list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Pure function: one provider row -> (siret, siren, [(type, raw, normalized)], [(issue_code, detail)]).

    Invalid values are dropped and reported; the row stays usable if at least one valid contact value remains.
    """
    issues: list[tuple[str, str]] = []
    siret = "".join(ch for ch in _cell(row, mapping.get("siret")) if ch.isdigit())
    siren = "".join(ch for ch in _cell(row, mapping.get("siren")) if ch.isdigit())
    if siret and not is_valid_siret(siret):
        issues.append(("INVALID_SIRET", siret))
        siret = ""
    if siret and not siren:
        siren = siret[:9]
    if siren and not is_valid_siren(siren):
        issues.append(("INVALID_SIREN", siren))
        siren = ""
    contacts: list[tuple[str, str, str]] = []
    for col in mapping.get("phones") or []:
        raw = _cell(row, col)
        if raw:
            norm = normalize_phone_fr(raw)
            if norm:
                contacts.append(("phone", raw, norm))
            else:
                issues.append(("INVALID_PHONE", raw))
    for col in mapping.get("emails") or []:
        raw = _cell(row, col)
        if raw:
            norm = normalize_email(raw)
            if norm:
                contacts.append(("email", raw, norm))
            else:
                issues.append(("INVALID_EMAIL", raw))
    raw = _cell(row, mapping.get("website"))
    if raw:
        dom = normalize_domain(raw)
        if dom:
            contacts.append(("website", raw, dom))
        else:
            issues.append(("INVALID_WEBSITE", raw))
    raw = _cell(row, mapping.get("linkedin"))
    if raw:
        if "linkedin.com/company/" in raw.lower():
            contacts.append(("linkedin", raw, raw.lower().rstrip("/")))
        else:
            issues.append(("INVALID_LINKEDIN", raw))
    # de-duplicate within the row (same type + normalized value)
    seen, uniq = set(), []
    for c in contacts:
        if (c[0], c[2]) not in seen:
            seen.add((c[0], c[2]))
            uniq.append(c)
    return (siret or None), (siren or None), uniq, issues


def _read_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
        yield from enumerate(csv.DictReader(fh, dialect=dialect), start=2)


def _match(cur, siret: Optional[str], siren: Optional[str]):
    """Return (company_id, establishment_id|None, status, is_diffusible) or None. SIRET first, then SIREN."""
    if siret:
        cur.execute(
            "SELECT c.company_id, a.establishment_id, c.status, c.is_diffusible FROM company_addresses a "
            "JOIN companies c ON c.company_id = a.company_id WHERE a.siret = %s", (siret,))
        r = cur.fetchone()
        if r:
            return r
    if siren:
        cur.execute("SELECT company_id, NULL::bigint, status, is_diffusible FROM companies WHERE siren = %s", (siren,))
        return cur.fetchone()
    return None


def store_contact(cur, company_id, establishment_id, ctype, raw, norm, status, source_id, batch_id) -> str:
    """Insert or refresh one contact. Returns 'added' or 'unchanged'."""
    cur.execute(
        "SELECT contact_id FROM company_contacts WHERE company_id = %s AND contact_type = %s AND value_normalized = %s",
        (company_id, ctype, norm))
    existing = cur.fetchone()
    if existing:
        cur.execute("UPDATE company_contacts SET last_verified_at = now() WHERE contact_id = %s", (existing[0],))
        return "unchanged"
    cur.execute(
        "SELECT 1 FROM company_contacts WHERE company_id = %s AND contact_type = %s "
        "AND establishment_id IS NOT DISTINCT FROM %s AND is_primary", (company_id, ctype, establishment_id))
    has_primary = cur.fetchone() is not None
    if ctype in ("phone", "email"):
        contact_conflict(cur, company_id, ctype, norm, source_id, status, establishment_id)
    if ctype == "website":
        # a company has one official website: a different value from another source is kept for review, not dropped
        cur.execute(
            "SELECT value_normalized, source_id FROM company_contacts WHERE company_id = %s AND contact_type = 'website' "
            "AND value_normalized <> %s ORDER BY is_primary DESC, contact_id LIMIT 1", (company_id, norm))
        other = cur.fetchone()
        if other:
            record_conflict(cur, company_id, "website", other[0], other[1], norm, source_id, establishment_id)
    cur.execute(
        "INSERT INTO company_contacts (company_id, establishment_id, contact_type, value, value_normalized, is_primary, "
        "value_status, source_id, last_verified_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())",
        (company_id, establishment_id, ctype, raw, norm, not has_primary, status, source_id))
    cur.execute(
        "INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, "
        "source_id, batch_id) VALUES (%s, 'company_contacts', %s, NULL, %s, 'insert', %s, %s)",
        (company_id, ctype, norm, source_id, batch_id))
    return "added"


def run(dsn: str, path: Path, contract_ref: Optional[str], mapping: Optional[dict] = None, status: str = "sourced",
        reject_dir: Path = Path("reports")) -> dict:
    contract_ref = (contract_ref or os.environ.get("LICENSED_CONTACTS_CONTRACT_REF") or "").strip()
    if not contract_ref:
        raise LicenceError("No contract reference: licensed contact data may only be loaded under a signed licence "
                           "(--contract-ref or LICENSED_CONTACTS_CONTRACT_REF).")
    if status not in ("sourced", "verified"):
        raise ValueError("status must be 'sourced' or 'verified'")
    mapping = {**DEFAULT_MAPPING, **(mapping or {})}
    stats = {"read": 0, "matched": 0, "contacts_added": 0, "contacts_unchanged": 0, "rejected": 0}
    reject_dir.mkdir(parents=True, exist_ok=True)
    reject_path = reject_dir / f"licensed_contacts_rejects_{date.today().isoformat()}.csv"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur, reject_path.open("w", newline="", encoding="utf-8-sig") as rf:
        rej = csv.DictWriter(rf, fieldnames=REJECT_FIELDS)
        rej.writeheader()
        cur.execute("SELECT source_id FROM data_sources WHERE code = 'licensed_contacts'")
        source_id = cur.fetchone()[0]
        base_note = f"licensed contacts file={path.name} contract={contract_ref}"
        cur.execute(
            "INSERT INTO import_export_batches (batch_type, source_id, notes) VALUES ('enrich', %s, %s) RETURNING batch_id",
            (source_id, base_note))
        batch_id = cur.fetchone()[0]
        conn.commit()
        outcome, note = "success", None

        def reject(line, siret, siren, code, detail):
            stats["rejected"] += 1
            rej.writerow({"line": line, "siret": siret or "", "siren": siren or "", "code": code, "detail": detail})

        try:
            for n, (line, row) in enumerate(_read_rows(path), 1):
                stats["read"] += 1
                siret, siren, contacts, issues = parse_row(row, mapping)
                for code, detail in issues:
                    reject(line, siret, siren, code, detail)
                if not siret and not siren:
                    reject(line, None, None, "NO_VALID_ID", "no valid SIRET/SIREN")
                    continue
                if not contacts:
                    continue
                m = _match(cur, siret, siren)
                if m is None:
                    reject(line, siret, siren, "NO_MATCH", "SIRET/SIREN not in the register")
                    continue
                company_id, establishment_id, c_status, diffusible = m
                if not diffusible:
                    reject(line, siret, siren, "NON_DIFFUSIBLE", "company is non-diffusible: not enriched")
                    continue
                if c_status != "active":
                    reject(line, siret, siren, "NOT_ACTIVE", f"company status is {c_status}")
                    continue
                stats["matched"] += 1
                for ctype, raw, norm in contacts:
                    res = store_contact(cur, company_id, establishment_id, ctype, raw, norm, status, source_id, batch_id)
                    stats["contacts_added" if res == "added" else "contacts_unchanged"] += 1
                if n % COMMIT_EVERY == 0:
                    conn.commit()
            if stats["rejected"]:
                outcome, note = "partial", f"{base_note}; {stats['rejected']} rejects, see {reject_path.name}"
        except Exception as exc:
            outcome, note = "failed", f"{base_note}; {str(exc)[:900]}"
            raise
        finally:
            conn.commit()
            cur.execute(
                "UPDATE import_export_batches SET status = %s, finished_at = now(), rows_read = %s, rows_added = %s, "
                "rows_unchanged = %s, rows_review = %s, notes = coalesce(%s, notes) WHERE batch_id = %s",
                (outcome, stats["read"], stats["contacts_added"], stats["contacts_unchanged"], stats["rejected"], note, batch_id))
            conn.commit()
    stats["reject_file"] = str(reject_path)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--contract-ref", help="licence/contract reference (or env LICENSED_CONTACTS_CONTRACT_REF)")
    ap.add_argument("--mapping", type=Path, help="JSON column mapping for this provider")
    ap.add_argument("--status", default="sourced", choices=["sourced", "verified"])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mapping = json.loads(args.mapping.read_text(encoding="utf-8")) if args.mapping else None
    print(run(os.environ["DATABASE_URL"], args.file, args.contract_ref, mapping, args.status))


if __name__ == "__main__":
    main()
