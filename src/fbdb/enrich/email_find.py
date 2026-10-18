"""Find the direct email of a named person (Hunter Email Finder). PAID: one credit per email found, hard DAILY cap.

Who is searched: ACTIVE interlocutors with a first and last name, no email linked yet, whose company has a website PROVEN by SIREN
(the domain comes from that proven site, never from a guess). By default only function_category = director (--categories to widen).
It never builds prenom.nom@domain by itself: the address must come from the provider, with its own verification status.

Hunter: GET https://api.hunter.io/v2/email-finder?domain=..&first_name=..&last_name=..&api_key=.., answer
{"data": {"email": str|null, "score": 0-100, "sources": [...], "verification": {"status": valid|accept_all|webmail|unknown|invalid|...}}}.
NOT verified against a live key from the build sandbox (blocked): run `--limit 5` and read the log first. Cap: HUNTER_FINDER_DAILY_CAP (default 25).

What is stored (company_contacts, source 'hunter', linked to the person with evidence `provider_attributed`):
  verification valid                  -> value_status 'verified'  (technically deliverable; identity still only provider-attributed)
  invalid / disposable                -> NOT stored (outcome 'invalid')
  accept_all / webmail / unknown / -  -> value_status 'inferred'  (a hypothesis: not exported until qualified)
An address opposed on the email channel is never stored. The attempt (found / not_found / invalid / opposed / already_known) is
remembered in email_find_attempts so the same person is not paid for again before --refresh-days (default 180).

Usage: python -m fbdb.enrich.email_find [--limit 25] [--categories director,sales] [--refresh-days 180]. Needs DATABASE_URL, HUNTER_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

import httpx
import psycopg
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fbdb.common import usage
from fbdb.enrich.email_verify import QuotaExhausted, RetryableError, decide
from fbdb.enrich.interlocutors import link_contact
from fbdb.transform.normalize import normalize_email

log = logging.getLogger("fbdb.email_find")
BASE = os.environ.get("HUNTER_FINDER_BASE", "https://api.hunter.io/v2/email-finder")
DAILY_CAP = int(os.environ.get("HUNTER_FINDER_DAILY_CAP", "25"))
PROVIDER = "hunter_finder"
CATEGORIES = ("director", "sales", "project_manager", "admin", "technical", "other", "unknown")


class HunterFinder:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0, transport=None):
        self._key = api_key or os.environ["HUNTER_API_KEY"]
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(3), reraise=True)
    def find(self, first_name: str, last_name: str, domain: str) -> Optional[dict]:
        """{"email", "score", "status", "sources"} or None when the provider has no address for this person."""
        r = self._http.get(BASE, params={"domain": domain, "first_name": first_name, "last_name": last_name, "api_key": self._key})
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Hunter HTTP {r.status_code}: invalid key or no credits left")
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        data = r.json().get("data") or {}
        if not data.get("email"):
            return None
        return {"email": data["email"], "score": data.get("score"), "status": (data.get("verification") or {}).get("status"),
                "sources": len(data.get("sources") or [])}


SELECT_DUE = """
SELECT i.interlocutor_id, i.company_id, i.first_name, i.last_name, i.function_category, w.domain
FROM company_interlocutors i
JOIN companies c ON c.company_id = i.company_id AND c.status = 'active' AND c.is_diffusible
JOIN LATERAL (SELECT k.value_normalized AS domain FROM company_contacts k WHERE k.company_id = i.company_id
                AND k.contact_type = 'website' AND k.match_proof IS NOT NULL AND k.value_status IN ('verified','sourced')
              ORDER BY (k.value_status = 'verified') DESC, k.is_primary DESC, k.contact_id LIMIT 1) w ON TRUE
LEFT JOIN email_find_attempts a ON a.interlocutor_id = i.interlocutor_id
WHERE i.is_active AND coalesce(i.first_name, '') <> '' AND i.function_category = ANY(%(cats)s)
  AND NOT EXISTS (SELECT 1 FROM company_contacts k WHERE k.interlocutor_id = i.interlocutor_id AND k.contact_type = 'email'
                  AND k.value_status <> 'unavailable')
  AND (a.searched_at IS NULL OR a.searched_at < now() - make_interval(days => %(days)s))
ORDER BY i.decision_maker_confirmed DESC, (i.function_category = 'director') DESC, a.searched_at NULLS FIRST, i.interlocutor_id
LIMIT %(limit)s
"""


def _remember(cur, interlocutor_id: int, domain: str, outcome: str, score=None) -> None:
    cur.execute(
        """INSERT INTO email_find_attempts (interlocutor_id, searched_at, domain, outcome, score) VALUES (%s, now(), %s, %s, %s)
           ON CONFLICT (interlocutor_id) DO UPDATE SET searched_at = now(), domain = EXCLUDED.domain, outcome = EXCLUDED.outcome,
             score = EXCLUDED.score""", (interlocutor_id, domain, outcome, score))


def store_found(cur, row: tuple, found: Optional[dict], source_id: int) -> str:
    """Apply one provider answer. Returns the outcome stored in email_find_attempts."""
    interlocutor_id, company_id, _first, _last, category, domain = row
    if not found:
        _remember(cur, interlocutor_id, domain, "not_found")
        return "not_found"
    email = normalize_email(found["email"])
    score = int(found["score"]) if found.get("score") is not None else None
    new_status, verdict = decide(found.get("status"))
    if not email or new_status == "unavailable":
        _remember(cur, interlocutor_id, domain, "invalid", score)
        return "invalid"
    cur.execute("SELECT 1 FROM contact_oppositions WHERE contact_type = 'email' AND value_normalized = %s AND channel = 'email'", (email,))
    if cur.fetchone():
        _remember(cur, interlocutor_id, domain, "opposed", score)
        return "opposed"
    cur.execute("SELECT contact_id, interlocutor_id FROM company_contacts WHERE company_id = %s AND contact_type = 'email' "
                "AND value_normalized = %s", (company_id, email))
    existing = cur.fetchone()
    use = "direction" if category == "director" else None
    if existing:
        if existing[1] is None:           # the company already held it, nobody owned it: the provider attributes it to this person
            try:
                link_contact(cur, company_id, interlocutor_id, "email", email, "provider_attributed", use)
            except ValueError as exc:
                log.info("not linked %s: %s", email, exc)
        _remember(cur, interlocutor_id, domain, "already_known", score)
        return "already_known"
    cur.execute("SELECT 1 FROM company_contacts WHERE company_id = %s AND contact_type = 'email' AND establishment_id IS NULL AND is_primary",
                (company_id,))
    has_primary = cur.fetchone() is not None
    proof = f"Hunter email-finder for {domain}: score {score}, {found.get('sources', 0)} public source(s), verification {verdict}"
    cur.execute(
        """INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status, source_id,
                 match_proof, verify_result, checked_at, last_verified_at)
           VALUES (%s,'email',%s,%s,%s,%s,%s,%s,%s, now(), CASE WHEN %s = 'verified' THEN now() END)""",
        (company_id, email, email, not has_primary, new_status or "inferred", source_id, proof, verdict, new_status or ""))
    link_contact(cur, company_id, interlocutor_id, "email", email, "provider_attributed", use)
    _remember(cur, interlocutor_id, domain, "found", score)
    return "found"


def run(dsn: str, client=None, limit: int = 25, categories=("director",), refresh_days: int = 180, cap: int = DAILY_CAP) -> dict:
    bad = [c for c in categories if c not in CATEGORIES]
    if bad:
        raise ValueError(f"unknown categories {bad}: use {CATEGORIES}")
    client = client or HunterFinder()
    stats = {"read": 0, "found": 0, "not_found": 0, "invalid": 0, "opposed": 0, "already_known": 0, "errors": 0,
             "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code = 'hunter'").fetchone()[0]
        for row in conn.execute(SELECT_DUE, {"cats": list(categories), "days": refresh_days, "limit": limit}).fetchall():
            if not usage.reserve(conn, PROVIDER, cap, 1):
                stats["cap_reached"] = True
                break
            stats["read"] += 1
            try:
                found = client.find(row[2], row[3], row[5])
            except QuotaExhausted as exc:
                usage.release(conn, PROVIDER, 1)
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                usage.release(conn, PROVIDER, 1)
                stats["errors"] += 1
                log.warning("email find failed for interlocutor %s: %s", row[0], exc)
                continue
            with conn.cursor() as cur:
                stats[store_found(cur, row, found, source_id)] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--categories", default="director", help="comma separated function categories")
    ap.add_argument("--refresh-days", type=int, default=180)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, categories=tuple(c.strip() for c in a.categories.split(",") if c.strip()),
            refresh_days=a.refresh_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
