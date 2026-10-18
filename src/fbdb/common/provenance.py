"""Helpers for provenance: conflicting values between sources and opposition a la prospection.

record_conflict(): keep the losing value of a source disagreement (never silently overwritten).
set_opposition(): record an opposition a la prospection PER CHANNEL, keyed by the value (table contact_oppositions).
  email value -> channel email;  phone value -> channels call and sms (independent).
  Keyed by value, not by contact row: it also covers rows added later and values we do not hold yet. The HubSpot export view,
  and the paid verifiers, read this table.
CLI: python -m fbdb.common.provenance oppose --value jean@x.fr [--channel email|call|sms|all ...] [--note "asked by email 2026-10-01"]
     python -m fbdb.common.provenance status --value 0612345678
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


CHANNELS = {"email": ("email",), "phone": ("call", "sms")}


def _channels(ctype: str, channels) -> tuple[str, ...]:
    """Validated channels for this contact type; None or 'all' means every channel the value can be used on."""
    allowed = CHANNELS[ctype]
    if not channels or "all" in channels:
        return allowed
    bad = [c for c in channels if c not in allowed]
    if bad:
        raise ValueError(f"channel {bad} not valid for a {ctype}: use {list(allowed)} or 'all'")
    return tuple(dict.fromkeys(channels))


def set_opposition(conn, value: str, note: str | None = None, channels=None) -> int:
    """Records the opposition of this email/phone on the given channels (default: all its channels).

    Applies to every company, now and later. Idempotent: an existing opposition keeps its original date.
    Returns the number of channels newly opposed.
    """
    parsed = normalize_contact(value)
    if not parsed:
        raise ValueError("not a valid email or French phone number")
    ctype, norm = parsed
    n = 0
    for ch in _channels(ctype, channels):
        n += conn.execute(
            """INSERT INTO contact_oppositions (contact_type, value_normalized, channel, note) VALUES (%s,%s,%s,%s)
               ON CONFLICT (contact_type, value_normalized, channel) DO NOTHING""", (ctype, norm, ch, note)).rowcount
    return n


def opposed_channels(conn, value: str) -> list[str]:
    """Channels on which this value is opposed (also honours the legacy per-row flag: every channel)."""
    parsed = normalize_contact(value)
    if not parsed:
        raise ValueError("not a valid email or French phone number")
    ctype, norm = parsed
    rows = conn.execute("SELECT channel FROM contact_oppositions WHERE contact_type = %s AND value_normalized = %s",
                        (ctype, norm)).fetchall()
    out = {r[0] for r in rows}
    if conn.execute("SELECT 1 FROM company_contacts WHERE contact_type = %s AND value_normalized = %s AND opposed_at IS NOT NULL LIMIT 1",
                    (ctype, norm)).fetchone():
        out |= set(CHANNELS[ctype])
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["oppose", "status"])
    ap.add_argument("--value", required=True)
    ap.add_argument("--channel", action="append", choices=["email", "call", "sms", "all"],
                    help="repeatable; default: every channel of the value (email -> email; phone -> call and sms)")
    ap.add_argument("--note")
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if a.action == "oppose":
            print({"channels_added": set_opposition(conn, a.value, a.note, a.channel)})
        else:
            print({"opposed_channels": opposed_channels(conn, a.value)})


if __name__ == "__main__":
    main()
