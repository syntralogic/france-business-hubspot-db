"""Mailbox function ("fonction de la boite") and priority email (client spec, section 7).

What is stored, and what is NOT:
  * `company_contacts.contact_use` = function of the mailbox: direction / sales (devis) / general / after_sales (SAV) /
    accounting / recruitment / emergency / switchboard. `contact_use_basis` says HOW it was decided:
      address_name  read from the address itself (devis@, sav@, direction@ ...)       - free, deterministic
      page_context  read from the words around the value on the proven site          - set by fbdb.enrich.website
      manual        a person set it here (set-use)                                    - the only "certain" basis
    A mailbox whose function cannot be read keeps NULL ("unknown"): nothing is guessed from a person's name or from the domain.
    A named mailbox (jean.dupont@) is NOT assumed to be the director's.
  * Which function is preferred is DATA (`email_priority_rules`, sql/003d): rank + eligible. The defaults are an assumption
    (direction > devis > general > switchboard > unknown; SAV, urgence, comptabilite, recrutement never "priority"). Change them with
    `set-rule`; the client decides.
  * The priority email of a company = view `v_priority_email` (company level). The HubSpot view picks per establishment with the
    same rules and exposes `email_use` / `email_is_priority` (not exported to the CSV). `is_primary` ("first loaded") is NOT changed.

CLI (needs DATABASE_URL):
  python -m fbdb.enrich.email_priority backfill-use [--dry-run]        # derive the function from the address of emails that have none
  python -m fbdb.enrich.email_priority set-use --siren 552100554 --email devis@exemple.fr --use sales
  python -m fbdb.enrich.email_priority rules
  python -m fbdb.enrich.email_priority set-rule --use after_sales --rank 6 --eligible no
  python -m fbdb.enrich.email_priority show --siren 552100554          # every email, its function, basis, rank and which one is THE priority
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

import psycopg

from fbdb.enrich.site_extract import email_use
from fbdb.transform.normalize import is_valid_siren, normalize_email

USES = ("switchboard", "direction", "sales", "emergency", "after_sales", "accounting", "recruitment", "general")
RULE_KEYS = USES + ("unknown",)
BATCH = 2000


def backfill_use(dsn: str, dry_run: bool = False) -> dict:
    """Give a function to the emails that have none, from the ADDRESS only. Rows that already have a function are never touched."""
    stats = {"scanned": 0, "set": 0, "unreadable": 0, "by_use": {}}
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT contact_id, value_normalized FROM company_contacts WHERE contact_type = 'email' "
                            "AND contact_use IS NULL ORDER BY contact_id").fetchall()
        for contact_id, norm in rows:
            stats["scanned"] += 1
            use = email_use(norm)
            if not use:
                stats["unreadable"] += 1
                continue
            stats["set"] += 1
            stats["by_use"][use] = stats["by_use"].get(use, 0) + 1
            if not dry_run:
                conn.execute("UPDATE company_contacts SET contact_use = %s, contact_use_basis = 'address_name' "
                             "WHERE contact_id = %s AND contact_use IS NULL", (use, contact_id))
            if stats["scanned"] % BATCH == 0 and not dry_run:
                conn.commit()
        if not dry_run:
            conn.commit()
    return stats


def set_use(cur, siren: str, email: str, use: str) -> dict:
    """A person states the function of one stored email. Basis 'manual'; the old value goes to company_change_history."""
    if use not in USES:
        raise ValueError(f"use must be one of {', '.join(USES)}")
    norm = normalize_email(email)
    if not norm:
        raise ValueError("not a valid email")
    if not is_valid_siren(siren):
        raise ValueError("invalid SIREN")
    cur.execute("SELECT k.contact_id, k.company_id, k.contact_use FROM company_contacts k JOIN companies c USING (company_id) "
                "WHERE c.siren = %s AND k.contact_type = 'email' AND k.value_normalized = %s", (siren, norm))
    row = cur.fetchone()
    if not row:
        raise LookupError(f"{norm} is not stored for SIREN {siren} (it is never created here)")
    contact_id, company_id, old = row
    cur.execute("UPDATE company_contacts SET contact_use = %s, contact_use_basis = 'manual' WHERE contact_id = %s", (use, contact_id))
    cur.execute("SELECT source_id FROM data_sources WHERE code = 'manual'")
    src = cur.fetchone()
    cur.execute("INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, source_id) "
                "VALUES (%s, 'company_contacts', 'contact_use', %s, %s, 'update', %s)", (company_id, old, use, src[0] if src else None))
    return {"contact_id": contact_id, "email": norm, "old_use": old, "new_use": use}


def set_rule(cur, use: str, rank: int, eligible: bool, note: Optional[str] = None) -> None:
    if use not in RULE_KEYS:
        raise ValueError(f"use must be one of {', '.join(RULE_KEYS)}")
    if not 1 <= rank <= 99:
        raise ValueError("rank must be between 1 and 99")
    cur.execute("INSERT INTO email_priority_rules (contact_use, priority_rank, eligible, note) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (contact_use) DO UPDATE SET priority_rank = EXCLUDED.priority_rank, eligible = EXCLUDED.eligible, "
                "note = COALESCE(EXCLUDED.note, email_priority_rules.note)", (use, rank, eligible, note))


def show(cur, siren: str) -> list[dict]:
    """Every stored email of the company, with function, basis, rank and whether it is THE priority email (v_priority_email)."""
    cur.execute(
        """SELECT k.value_normalized, k.value_status, k.contact_use, k.contact_use_basis, r.priority_rank, r.eligible,
                  (k.opposed_at IS NOT NULL OR EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'email'
                     AND o.value_normalized = k.value_normalized AND o.channel = 'email')) AS opposed,
                  (p.contact_id IS NOT NULL) AS is_priority
           FROM company_contacts k JOIN companies c USING (company_id)
           LEFT JOIN email_priority_rules r ON r.contact_use = COALESCE(k.contact_use, 'unknown')
           LEFT JOIN v_priority_email p ON p.contact_id = k.contact_id
           WHERE c.siren = %s AND k.contact_type = 'email' ORDER BY is_priority DESC, r.priority_rank NULLS LAST, k.contact_id""", (siren,))
    cols = ("email", "status", "use", "basis", "rank", "eligible", "opposed", "is_priority")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill-use"); b.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("set-use"); s.add_argument("--siren", required=True); s.add_argument("--email", required=True)
    s.add_argument("--use", required=True, choices=USES)
    sub.add_parser("rules")
    r = sub.add_parser("set-rule"); r.add_argument("--use", required=True, choices=RULE_KEYS)
    r.add_argument("--rank", type=int, required=True); r.add_argument("--eligible", required=True, choices=("yes", "no")); r.add_argument("--note")
    sh = sub.add_parser("show"); sh.add_argument("--siren", required=True)
    args = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    if args.cmd == "backfill-use":
        print(backfill_use(dsn, args.dry_run))
        return
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if args.cmd == "set-use":
            print(set_use(cur, args.siren, args.email, args.use))
        elif args.cmd == "set-rule":
            set_rule(cur, args.use, args.rank, args.eligible == "yes", args.note)
            print("rule saved")
        elif args.cmd == "rules":
            cur.execute("SELECT contact_use, priority_rank, eligible, note FROM email_priority_rules ORDER BY priority_rank")
            for row in cur.fetchall():
                print(row)
        elif args.cmd == "show":
            for row in show(cur, args.siren):
                print(row)
        conn.commit()


if __name__ == "__main__":
    main()
