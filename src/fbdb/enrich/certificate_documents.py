"""Attestation documents (RC pro, decennale) and certificate copies attached to a certification row (client spec, section 11).

What this does, and what it does not:
  * A document is a FILE someone gives us (the insurer's attestation, the client's copy of a certificate). It is stored with its
    SHA-256, size, who supplied it and when, and attached to ONE `company_certifications` row. Nothing is downloaded from the web
    and the content is never read, summarised or OCR'd: the dates and the label are typed in (or come from the manifest) by the person
    who read the document.
  * Only PDF, PNG and JPEG (checked on the first bytes, not on the extension), 10 MB at most. Anything else is refused.
  * `kind = attestation` is for RC pro and Decennale rows only; `kind = certificate` (a copy of a certificate) for any type.
  * If the company has no matching certification row yet, a row is created ONLY when `--valid-to` is given (an attestation without
    an end date cannot be used); it is stored with source `manual` and written to company_change_history. If several rows match,
    nothing is attached and the candidates are listed: narrow with --label / --valid-from / --valid-to.
  * Same file attached twice to the same row = one document ("unchanged"). Only active, diffusible companies.
  * The file is never exported to HubSpot and never exposed by the API. Views `v_company_certificates` and `v_attestation_status`
    show metadata only. Attestations can carry personal data of a sole proprietor: retention and legal basis are a client / DPO decision.
  * Stored in PostgreSQL (bytea): fine for hundreds or a few thousand attestations. Beyond that, move the files to object storage
    and keep sha256 + a reference (not built).

CLI (needs DATABASE_URL):
  python -m fbdb.enrich.certificate_documents attach --siren 552100554 --type "RC pro" --file attestation.pdf --received-from "Insurer X" \\
        --valid-from 2026-01-01 --valid-to 2026-12-31 [--siret ...] [--label ...] [--kind attestation|certificate] [--note ...]
  python -m fbdb.enrich.certificate_documents load-manifest manifest.csv --received-from "Client, October 2026 batch"
        manifest columns: siren | siret, cert_type, file (relative to the manifest), valid_from, valid_to, label, kind, note
  python -m fbdb.enrich.certificate_documents list --siren 552100554
  python -m fbdb.enrich.certificate_documents get --document-id 12 --out /tmp/attestation.pdf
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg

from fbdb.enrich.certifications import normalize_cert_type, parse_date
from fbdb.transform.normalize import is_valid_siren, is_valid_siret

MAX_BYTES = 10 * 1024 * 1024
KINDS = ("attestation", "certificate")
ATTESTATION_TYPES = ("RC pro", "Decennale")
REJECT_FIELDS = ["line", "siren", "cert_type", "file", "code", "detail"]


class DocumentError(ValueError):
    """The document cannot be attached; the message says why (never silently skipped)."""


def sniff_mime(head: bytes) -> Optional[str]:
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def inspect_file(path: str | Path) -> dict:
    """Read a file and check it: not empty, <= 10 MB, really a PDF / PNG / JPEG. Returns {content, mime, size, sha256, name}."""
    p = Path(path)
    if not p.is_file():
        raise DocumentError(f"file not found: {p}")
    size = p.stat().st_size
    if size == 0:
        raise DocumentError("file is empty")
    if size > MAX_BYTES:
        raise DocumentError(f"file is {size} bytes, the limit is {MAX_BYTES}")
    content = p.read_bytes()
    mime = sniff_mime(content[:16])
    if not mime:
        raise DocumentError("not a PDF, PNG or JPEG (checked on the first bytes)")
    return {"content": content, "mime": mime, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "name": p.name}


def default_kind(cert_type: str) -> str:
    return "attestation" if cert_type in ATTESTATION_TYPES else "certificate"


def attach(cur, siren: str, cert_type_raw: str, path: str | Path, received_from: str, *, kind: Optional[str] = None,
           siret: Optional[str] = None, label: Optional[str] = None, valid_from: Optional[date] = None,
           valid_to: Optional[date] = None, note: Optional[str] = None) -> dict:
    """Attach one file to a certification row. Returns {status: added|unchanged, document_id, certification_id, certification_created}."""
    if not (received_from or "").strip():
        raise DocumentError("received_from is required (who supplied the file)")
    if not is_valid_siren(siren):
        raise DocumentError(f"invalid SIREN {siren}")
    if siret and (not is_valid_siret(siret) or not siret.startswith(siren)):
        raise DocumentError(f"invalid SIRET {siret} for SIREN {siren}")
    ctype = normalize_cert_type(cert_type_raw)
    if not ctype:
        raise DocumentError(f"unknown certification type: {cert_type_raw!r}")
    kind = kind or default_kind(ctype)
    if kind not in KINDS:
        raise DocumentError(f"kind must be one of {', '.join(KINDS)}")
    if kind == "attestation" and ctype not in ATTESTATION_TYPES:
        raise DocumentError(f"an attestation is for {' / '.join(ATTESTATION_TYPES)} only; use kind 'certificate' for {ctype}")
    if valid_from and valid_to and valid_to < valid_from:
        raise DocumentError(f"valid_to {valid_to} is before valid_from {valid_from}")
    f = inspect_file(path)
    cur.execute("SELECT company_id FROM companies WHERE siren = %s AND status = 'active' AND is_diffusible", (siren,))
    comp = cur.fetchone()
    if not comp:
        raise DocumentError(f"SIREN {siren} is not an active, diffusible company in the base")
    company_id = comp[0]
    est_id = None
    if siret:
        cur.execute("SELECT establishment_id FROM company_addresses WHERE siret = %s AND company_id = %s", (siret, company_id))
        e = cur.fetchone()
        est_id = e[0] if e else None
    cur.execute(
        """SELECT certification_id, cert_label, valid_from, valid_to FROM company_certifications
           WHERE company_id = %s AND cert_type = %s AND (%s::text IS NULL OR cert_label = %s)
             AND (%s::date IS NULL OR valid_from = %s) AND (%s::date IS NULL OR valid_to = %s) ORDER BY certification_id""",
        (company_id, ctype, label, label, valid_from, valid_from, valid_to, valid_to))
    rows = cur.fetchall()
    created = False
    if len(rows) > 1:
        cand = "; ".join(f"#{r[0]} label={r[1] or '-'} {r[2] or '?'} -> {r[3] or '?'}" for r in rows)
        raise DocumentError(f"{len(rows)} certification rows match, narrow with --label / --valid-from / --valid-to: {cand}")
    if rows:
        cert_id = rows[0][0]
    else:
        if valid_to is None:
            raise DocumentError(f"no {ctype} row for SIREN {siren}: give --valid-to (read on the document) to create it, or load the certification file first")
        cur.execute("SELECT source_id FROM data_sources WHERE code = 'manual'")
        src = cur.fetchone()
        cur.execute("INSERT INTO company_certifications (company_id, establishment_id, cert_type, cert_label, valid_from, valid_to, source_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING certification_id",
                    (company_id, est_id, ctype, label, valid_from, valid_to, src[0] if src else None))
        cert_id = cur.fetchone()[0]
        created = True
        cur.execute("INSERT INTO company_change_history (company_id, establishment_id, table_name, field_name, old_value, new_value, change_type, source_id) "
                    "VALUES (%s,%s,'company_certifications','cert_type',NULL,%s,'insert',%s)", (company_id, est_id, ctype, src[0] if src else None))
    cur.execute(
        """INSERT INTO certification_documents (certification_id, kind, file_name, mime_type, size_bytes, sha256, content, received_from, note)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (certification_id, sha256) DO NOTHING RETURNING document_id""",
        (cert_id, kind, f["name"], f["mime"], f["size"], f["sha256"], f["content"], received_from.strip(), note))
    got = cur.fetchone()
    if got:
        return {"status": "added", "document_id": got[0], "certification_id": cert_id, "certification_created": created}
    cur.execute("SELECT document_id FROM certification_documents WHERE certification_id = %s AND sha256 = %s", (cert_id, f["sha256"]))
    return {"status": "unchanged", "document_id": cur.fetchone()[0], "certification_id": cert_id, "certification_created": created}


def load_manifest(dsn: str, manifest: str | Path, received_from: str, rejects_path: Optional[str] = None) -> dict:
    """Attach many files listed in a CSV. A bad line is rejected (reported), the others still load. Each line is its own transaction."""
    manifest = Path(manifest)
    stats = {"rows": 0, "added": 0, "unchanged": 0, "rejected": 0, "certifications_created": 0}
    rejects_path = rejects_path or str(manifest.with_suffix(".rejects.csv"))
    with psycopg.connect(dsn) as conn, open(manifest, newline="", encoding="utf-8-sig") as fh, \
            open(rejects_path, "w", newline="", encoding="utf-8") as rj:
        rejects = csv.DictWriter(rj, fieldnames=REJECT_FIELDS)
        rejects.writeheader()
        for i, row in enumerate(csv.DictReader(fh), start=2):
            stats["rows"] += 1
            get = lambda k: (row.get(k) or "").strip()
            siret = "".join(ch for ch in get("siret") if ch.isdigit())
            siren = "".join(ch for ch in get("siren") if ch.isdigit()) or siret[:9]
            try:
                vf, vt = parse_date(get("valid_from")), parse_date(get("valid_to"))
                if (get("valid_from") and vf is None) or (get("valid_to") and vt is None):
                    raise DocumentError(f"unreadable date: {get('valid_from') or get('valid_to')}")
                file_path = (manifest.parent / get("file")) if get("file") else None
                if file_path is None:
                    raise DocumentError("no file given")
                with conn.cursor() as cur:
                    res = attach(cur, siren, get("cert_type"), file_path, received_from, kind=get("kind") or None, siret=siret or None,
                                 label=get("label") or None, valid_from=vf, valid_to=vt, note=get("note") or None)
                conn.commit()
                stats[res["status"]] += 1
                stats["certifications_created"] += 1 if res["certification_created"] else 0
            except (DocumentError, psycopg.Error) as e:
                conn.rollback()
                stats["rejected"] += 1
                rejects.writerow({"line": i, "siren": siren, "cert_type": get("cert_type"), "file": get("file"), "code": "REJECTED", "detail": str(e)})
    stats["reject_file"] = rejects_path
    return stats


def list_documents(cur, siren: str) -> list[dict]:
    cur.execute(
        """SELECT d.document_id, t.cert_type, t.cert_label, t.valid_to, d.kind, d.file_name, d.mime_type, d.size_bytes, d.sha256,
                  d.received_from, d.received_at, d.note
           FROM certification_documents d JOIN company_certifications t USING (certification_id) JOIN companies c ON c.company_id = t.company_id
           WHERE c.siren = %s ORDER BY d.document_id""", (siren,))
    cols = ("document_id", "cert_type", "label", "valid_to", "kind", "file_name", "mime_type", "size_bytes", "sha256", "received_from", "received_at", "note")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_document(cur, document_id: int, out_path: str | Path) -> dict:
    """Write the stored file to disk (bytes identical to what was attached: the SHA-256 is verified before writing)."""
    cur.execute("SELECT content, sha256, file_name, mime_type FROM certification_documents WHERE document_id = %s", (document_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError(f"no document {document_id}")
    content = bytes(row[0])
    if hashlib.sha256(content).hexdigest() != row[1].strip():
        raise DocumentError("stored content does not match its SHA-256: not written")
    Path(out_path).write_bytes(content)
    return {"file_name": row[2], "mime_type": row[3], "bytes": len(content), "out": str(out_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("attach"); a.add_argument("--siren", required=True); a.add_argument("--type", required=True, dest="ctype")
    a.add_argument("--file", required=True); a.add_argument("--received-from", required=True); a.add_argument("--kind", choices=KINDS)
    a.add_argument("--siret"); a.add_argument("--label"); a.add_argument("--valid-from"); a.add_argument("--valid-to"); a.add_argument("--note")
    m = sub.add_parser("load-manifest"); m.add_argument("manifest"); m.add_argument("--received-from", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--siren", required=True)
    g = sub.add_parser("get"); g.add_argument("--document-id", type=int, required=True); g.add_argument("--out", required=True)
    args = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    if args.cmd == "load-manifest":
        print(load_manifest(dsn, args.manifest, args.received_from))
        return
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        try:
            if args.cmd == "attach":
                vf, vt = parse_date(args.valid_from or ""), parse_date(args.valid_to or "")
                if (args.valid_from and not vf) or (args.valid_to and not vt):
                    raise DocumentError("dates must be YYYY-MM-DD or DD/MM/YYYY")
                print(attach(cur, args.siren, args.ctype, args.file, args.received_from, kind=args.kind, siret=args.siret,
                             label=args.label, valid_from=vf, valid_to=vt, note=args.note))
            elif args.cmd == "list":
                for row in list_documents(cur, args.siren):
                    print(row)
            elif args.cmd == "get":
                print(get_document(cur, args.document_id, args.out))
        except (DocumentError, LookupError) as e:
            raise SystemExit(f"refused: {e}")
        conn.commit()


if __name__ == "__main__":
    main()
