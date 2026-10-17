"""Helpers for provenance: conflicting values between sources and opposition a la prospection.

record_conflict(): keep the losing value of a source disagreement (never silently overwritten).
set_opposition(): mark every email/phone contact matching a value as opposed; the HubSpot export view then drops it.
CLI: python -m fbdb.common.provenance oppose --value jean@x.fr [--note "asked by email 2026-10-01"]
"""
from __future__ import annotations

import argparse
import os

import psycopg

from fbdb.transform.normalize import normalize_email, normalize_phone_fr


def record_conflict(conn, company_id: int, field_name: str, kept_value, kept_source_id, other_value, other_source_id,
                    establishment_id=None) -> None:
    if str(kept_value) == str(other_value):
        return
    conn.execute(
        """INSERT INTO field_conflicts (company_id, establishment_id, field_name, kept_value, kept_source_id, other_value, other_source_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (company_id, field_name, other_value, other_source_id) DO NOTHING""",
        (company_id, establishment_id, field_name, str(kept_value), kept_source_id, str(other_value), other_source_id))


def normalize_contact(value: str):
    """(contact_type, normalized) for an email or a French phone; None when neither."""
    if "@" in value:
        n = normalize_email(value)
        return ("email", n) if n else None
    n = normalize_phone_fr(value)
    return ("phone", n) if n else None


def set_opposition(conn, value: str, note: str | None = None) -> int:
    """Marks all contacts with this email/phone as opposed (all companies). Returns rows changed."""
    parsed = normalize_contact(value)
    if not parsed:
        raise ValueError("not a valid email or French phone number")
    cur = conn.execute(
        """UPDATE company_contacts SET opposed_at = COALESCE(opposed_at, now()), opposition_note = %s
           WHERE contact_type = %s AND value_normalized = %s""", (note, parsed[0], parsed[1]))
    return cur.rowcount


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["oppose"])
    ap.add_argument("--value", required=True)
    ap.add_argument("--note")
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        print({"rows": set_opposition(conn, a.value, a.note)})


if __name__ == "__main__":
    main()
