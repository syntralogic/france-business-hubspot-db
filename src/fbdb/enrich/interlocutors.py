"""Interlocutors: several people per company, each with their own phones/emails (client spec, section 5).

Rules taken from the spec (enforced here AND by constraints in sql/013_interlocutors.sql):
  * A phone/email is linked to a person only with an evidence level: published_with_name / provider_attributed /
    confirmed_by_contact. A bare number on a site stays "company - person not identified" (no link).
  * `decision_maker_confirmed` needs a note and a date: a job title alone never proves it, only a conversation does.
  * Legal role (register) and operational function (team page) are separate fields.
  * Register directors are copied as interlocutors without inventing any contact value.

CLI (needs DATABASE_URL):
  python -m fbdb.enrich.interlocutors sync-directors [--limit N]
  python -m fbdb.enrich.interlocutors add --siren 552100554 --last-name Martin --first-name Luc --function "Responsable commercial" [--source-url URL]
  python -m fbdb.enrich.interlocutors link --siren 552100554 --value 0612345678 --last-name Martin --evidence published_with_name [--use sales]
  python -m fbdb.enrich.interlocutors confirm --siren 552100554 --last-name Martin --note "confirmed on call 2026-10-20"
  python -m fbdb.enrich.interlocutors list --siren 552100554
"""
from __future__ import annotations

import argparse
import os
import unicodedata
from typing import Optional

import psycopg

from fbdb.common.provenance import normalize_contact

EVIDENCE = ("published_with_name", "provider_attributed", "confirmed_by_contact")
USES = ("switchboard", "direction", "sales", "emergency", "after_sales", "accounting", "recruitment", "general")

# order matters: first match wins ("directeur commercial" is sales, not director)
_CATEGORIES = [
    ("sales", ("commercial", "vente", "business developer", "developpement commercial")),
    ("project_manager", ("charge d'affaires", "charge d affaires", "conducteur de travaux", "chef de projet", "chef de chantier",
                         "responsable de projet", "responsable d'affaires")),
    ("admin", ("administratif", "comptab", "secretaire", "assistant", "rh ", "ressources humaines", "office manager")),
    ("technical", ("technique", "technicien", "bureau d'etudes", "ingenieur", "qualite", "securite", "sav", "maintenance")),
    ("director", ("gerant", "gerante", "president", "presidente", "dirigeant", "directeur general", "directrice generale",
                  "directeur", "directrice", "fondateur", "fondatrice", "patron", "pdg", "co-gerant", "associe")),
]


def _fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch))


def classify_function(text: Optional[str]) -> str:
    """Keyword classification of an operational function. Returns 'unknown' when empty, 'other' when no keyword matches."""
    if not text or not text.strip():
        return "unknown"
    t = _fold(text) + " "
    for category, words in _CATEGORIES:
        if any(w in t for w in words):
            return category
    return "other"


def upsert_interlocutor(cur, company_id: int, last_name: str, first_name: Optional[str] = None,
                        operational_function: Optional[str] = None, legal_role: Optional[str] = None,
                        director_id: Optional[int] = None, profile_url: Optional[str] = None,
                        match_proof: Optional[str] = None, source_code: str = "manual", source_url: Optional[str] = None,
                        source_date=None) -> int:
    """Insert or refresh one person. Idempotent on (company, last name, first name, function). Returns interlocutor_id."""
    last_name = (last_name or "").strip()
    if not last_name:
        raise ValueError("last_name is required")
    first_name = (first_name or "").strip() or None
    operational_function = (operational_function or "").strip() or None
    cur.execute("SELECT source_id FROM data_sources WHERE code = %s", (source_code,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown data source {source_code!r}")
    category = "director" if (legal_role and not operational_function) else classify_function(operational_function)
    cur.execute(
        """INSERT INTO company_interlocutors (company_id, director_id, first_name, last_name, legal_role, operational_function,
               function_category, profile_url, match_proof, source_id, source_url, source_date, last_verified_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (company_id, lower(last_name), lower(coalesce(first_name, '')), lower(coalesce(operational_function, '')))
           DO UPDATE SET last_verified_at = now(), is_active = TRUE,
               director_id = coalesce(EXCLUDED.director_id, company_interlocutors.director_id),
               legal_role = coalesce(EXCLUDED.legal_role, company_interlocutors.legal_role),
               profile_url = coalesce(EXCLUDED.profile_url, company_interlocutors.profile_url),
               match_proof = coalesce(EXCLUDED.match_proof, company_interlocutors.match_proof),
               source_url = coalesce(EXCLUDED.source_url, company_interlocutors.source_url),
               source_date = coalesce(EXCLUDED.source_date, company_interlocutors.source_date)
           RETURNING interlocutor_id""",
        (company_id, director_id, first_name, last_name, legal_role, operational_function, category, profile_url,
         match_proof, row[0], source_url, source_date))
    return cur.fetchone()[0]


def attach_person(cur, company_id: int, last_name: str, first_name: Optional[str], function: Optional[str],
                  match_proof: Optional[str] = None, source_code: str = "team_page", source_url: Optional[str] = None):
    """Person read on a website: reuse the SAME person already known (for example the register director) instead of a duplicate.

    Returns (interlocutor_id, created). An existing person only gets the function/proof filled in when they had none.
    """
    cur.execute("SELECT interlocutor_id, operational_function FROM company_interlocutors WHERE company_id = %s "
                "AND lower(last_name) = lower(%s) AND lower(coalesce(first_name, '')) = lower(%s) "
                "ORDER BY is_active DESC, interlocutor_id LIMIT 1", (company_id, last_name, first_name or ""))
    row = cur.fetchone()
    if row:
        if row[1] is None and function:
            cur.execute("UPDATE company_interlocutors SET operational_function = %s, function_category = CASE WHEN function_category IN "
                        "('unknown','other') THEN %s ELSE function_category END, source_url = coalesce(source_url, %s), "
                        "match_proof = coalesce(match_proof, %s), last_verified_at = now() WHERE interlocutor_id = %s",
                        (function, classify_function(function), source_url, match_proof, row[0]))
        else:
            cur.execute("UPDATE company_interlocutors SET last_verified_at = now() WHERE interlocutor_id = %s", (row[0],))
        return row[0], False
    return upsert_interlocutor(cur, company_id, last_name, first_name, function, match_proof=match_proof,
                               source_code=source_code, source_url=source_url), True


def link_contact(cur, company_id: int, interlocutor_id: int, ctype: str, value: str, evidence: str,
                 use: Optional[str] = None) -> int:
    """Attach an EXISTING phone/email of the company to a person. Never creates the value and never downgrades evidence.

    Raises ValueError when the evidence is missing/unknown, the value is invalid, the person belongs to another company
    or the company does not hold this value (nothing is invented). Returns contact_id.
    """
    if evidence not in EVIDENCE:
        raise ValueError(f"evidence must be one of {EVIDENCE}: a person link without proof is not allowed")
    if use is not None and use not in USES:
        raise ValueError(f"use must be one of {USES}")
    parsed = normalize_contact(value)
    if not parsed or parsed[0] != ctype:
        raise ValueError(f"{value!r} is not a valid {ctype}")
    cur.execute("SELECT 1 FROM company_interlocutors WHERE interlocutor_id = %s AND company_id = %s", (interlocutor_id, company_id))
    if not cur.fetchone():
        raise ValueError("this person does not belong to this company")
    cur.execute("SELECT contact_id, interlocutor_id, attribution_level FROM company_contacts "
                "WHERE company_id = %s AND contact_type = %s AND value_normalized = %s", (company_id, ctype, parsed[1]))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"the company does not hold {parsed[1]}: load the value first, it is not created here")
    contact_id, current_person, current_level = row
    if current_person is not None and current_person != interlocutor_id:
        raise ValueError(f"{parsed[1]} is already linked to another person (interlocutor {current_person}): resolve by hand")
    rank = {None: 0, **{e: i + 1 for i, e in enumerate(EVIDENCE)}}
    level = evidence if rank[evidence] >= rank[current_level] else current_level
    cur.execute("UPDATE company_contacts SET interlocutor_id = %s, attribution_level = %s, contact_use = coalesce(%s, contact_use) "
                "WHERE contact_id = %s", (interlocutor_id, level, use, contact_id))
    return contact_id


def confirm_decision_maker(cur, interlocutor_id: int, note: str) -> None:
    """Mark a person as confirmed decision maker. Requires a non-empty note (what was said, when, by whom)."""
    if not (note or "").strip():
        raise ValueError("a confirmation note is required (a job title alone does not confirm a decision maker)")
    cur.execute("UPDATE company_interlocutors SET decision_maker_confirmed = TRUE, confirmation_note = %s, confirmed_at = now() "
                "WHERE interlocutor_id = %s", (note.strip(), interlocutor_id))
    if cur.rowcount != 1:
        raise ValueError(f"no interlocutor {interlocutor_id}")


def sync_directors(conn, limit: Optional[int] = None) -> dict:
    """Copy active register directors into company_interlocutors (legal role kept, no contact value invented).

    Directors who left (is_active = FALSE) mark their interlocutor inactive. Idempotent.
    """
    stats = {"created": 0, "refreshed": 0, "deactivated": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT d.director_id, d.company_id, d.first_name, d.last_name, d.role, s.code FROM company_directors d "
                    "LEFT JOIN data_sources s ON s.source_id = d.source_id WHERE d.is_active AND d.last_name IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM company_interlocutors i WHERE i.director_id = d.director_id AND i.last_verified_at > now() - interval '1 day') "
                    "ORDER BY d.director_id" + (f" LIMIT {int(limit)}" if limit else ""))
        for director_id, company_id, first, last, role, code in cur.fetchall():
            cur.execute("SELECT 1 FROM company_interlocutors WHERE director_id = %s", (director_id,))
            existed = cur.fetchone() is not None
            upsert_interlocutor(cur, company_id, last, first, legal_role=role, director_id=director_id,
                                match_proof="Register director (company_directors)", source_code=code or "annuaire")
            stats["refreshed" if existed else "created"] += 1
        cur.execute("UPDATE company_interlocutors i SET is_active = FALSE FROM company_directors d "
                    "WHERE i.director_id = d.director_id AND NOT d.is_active AND i.is_active")
        stats["deactivated"] = cur.rowcount
    conn.commit()
    return stats


def _company_id(cur, siren: str) -> int:
    cur.execute("SELECT company_id FROM companies WHERE siren = %s", (siren,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"unknown SIREN {siren}")
    return row[0]


def _person_id(cur, company_id: int, last_name: str, first_name: Optional[str] = None) -> int:
    cur.execute("SELECT interlocutor_id FROM company_interlocutors WHERE company_id = %s AND lower(last_name) = lower(%s) "
                "AND (%s::text IS NULL OR lower(first_name) = lower(%s::text)) ORDER BY is_active DESC, interlocutor_id",
                (company_id, last_name, first_name, first_name))
    rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"no interlocutor {last_name!r} in this company: add it first")
    if len(rows) > 1:
        raise SystemExit(f"{len(rows)} interlocutors match {last_name!r}: add --first-name")
    return rows[0][0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync-directors"); s.add_argument("--limit", type=int)
    a = sub.add_parser("add"); a.add_argument("--siren", required=True); a.add_argument("--last-name", required=True)
    a.add_argument("--first-name"); a.add_argument("--function"); a.add_argument("--profile-url"); a.add_argument("--source-url")
    a.add_argument("--proof"); a.add_argument("--source", default="manual", help="data source code (manual, team_page ...)")
    lk = sub.add_parser("link"); lk.add_argument("--siren", required=True); lk.add_argument("--value", required=True)
    lk.add_argument("--last-name", required=True); lk.add_argument("--first-name")
    lk.add_argument("--evidence", required=True, choices=EVIDENCE); lk.add_argument("--use", choices=USES)
    c = sub.add_parser("confirm"); c.add_argument("--siren", required=True); c.add_argument("--last-name", required=True)
    c.add_argument("--first-name"); c.add_argument("--note", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--siren", required=True)
    args = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    with psycopg.connect(dsn) as conn:
        if args.cmd == "sync-directors":
            print(sync_directors(conn, args.limit))
            return
        with conn.cursor() as cur:
            company_id = _company_id(cur, args.siren)
            if args.cmd == "add":
                print("interlocutor", upsert_interlocutor(cur, company_id, args.last_name, args.first_name, args.function,
                      profile_url=args.profile_url, match_proof=args.proof, source_code=args.source, source_url=args.source_url))
            elif args.cmd == "link":
                pid = _person_id(cur, company_id, args.last_name, args.first_name)
                ctype = normalize_contact(args.value)
                if not ctype:
                    raise SystemExit("value is neither a valid email nor a French phone")
                print("contact", link_contact(cur, company_id, pid, ctype[0], args.value, args.evidence, args.use))
            elif args.cmd == "confirm":
                pid = _person_id(cur, company_id, args.last_name, args.first_name)
                confirm_decision_maker(cur, pid, args.note)
                print("confirmed", pid)
            else:
                cur.execute("SELECT interlocutor_id, first_name, last_name, legal_role, operational_function, function_category, "
                            "decision_maker_confirmed, is_active, contact_type, value, attribution_level, contact_use "
                            "FROM v_interlocutor_contacts WHERE company_id = %s ORDER BY interlocutor_id, contact_type", (company_id,))
                for row in cur.fetchall():
                    print(row)
        conn.commit()


if __name__ == "__main__":
    main()
