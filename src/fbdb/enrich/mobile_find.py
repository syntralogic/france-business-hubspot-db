"""Direct mobile / direct line of a named person from a licensed provider (Lusha-type person enrichment). PAID, hard DAILY cap.

Why: the registers and the company's own site rarely give a director's mobile. This is the ONLY step that can attach one that nobody
published with the person's name, so it is held to the strictest rules:
 * LICENCE GATE: refuses to run without LUSHA_CONTRACT_REF (or --contract-ref): a written contract covering B2B prospecting, storage
   in the database / HubSpot and the lawful basis of the data must exist first (docs/14-licensed-contacts.md). The reference is kept in the proof.
 * Who: ACTIVE interlocutors with a first and last name, no mobile linked yet, whose company has a website PROVEN by SIREN (the domain
   comes from that proven site, never from a guess). Default: function_category = director only (--categories to widen).
 * Identity: the provider is asked first name + last name + company domain. If its answer names a company domain that is NOT ours the
   answer is dropped (`other_company`). The number stays PROVIDER-ATTRIBUTED (attribution_level `provider_attributed`), never "confirmed":
   nobody on our side has checked that the person owns it. Only a person's own confirmation can raise it (interlocutors `link --evidence`).
 * Only provider types Mobile and Direct are kept; a generic "Phone" (the company's switchboard) is ignored. A number the provider flags
   do-not-call is NOT stored. A number opposed on BOTH call and sms (contact_oppositions) is NOT stored. Invalid numbers are dropped.
 * Stored in company_contacts: contact_type 'phone', value_status 'sourced' (never 'verified': the line type / validity is checked later by
   phone_lookup, Twilio), source 'lusha', match_proof = what was asked and what the provider said + contract reference, NEVER the company's
   primary phone (is_primary FALSE), contact_use 'direction' for a director. A number the company already held is linked, not duplicated.
 * Memory: mobile_find_attempts (found / not_found / no_mobile / do_not_call / opposed / invalid / other_company / already_known): not queried
   again before --refresh-days (default 180). Daily cap LUSHA_DAILY_CAP (default 25); a failed call does not use the cap.

Provider call (from the public Lusha API documentation, v2): GET https://api.lusha.com/v2/person?firstName=..&lastName=..&companyDomain=..
&revealPhones=true, API key in a request header, answer {"contact": {"data": {"phoneNumbers": [{"number"|"internationalNumber", "type":
Mobile|Direct|Phone, "doNotCall": bool, "updateDate"}], "company": {"domain"}}, "isCreditCharged": bool}}.
**NOT verified against a live key from the build sandbox (blocked)**: the header name (LUSHA_AUTH_HEADER, default "api_key"), the exact field
names of a phone entry and whether revealPhones=true limits the credit are assumptions; the parser accepts several spellings. Run `--limit 5`,
read the log, and compare with a real answer before raising the limit. Nothing here has been run against a real provider contract.

Usage: python -m fbdb.enrich.mobile_find [--limit 25] [--categories director] [--refresh-days 180] [--contract-ref REF]
Needs DATABASE_URL, LUSHA_API_KEY, LUSHA_CONTRACT_REF.
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
from fbdb.enrich.email_find import CATEGORIES
from fbdb.enrich.email_verify import QuotaExhausted, RetryableError
from fbdb.enrich.interlocutors import link_contact
from fbdb.transform.normalize import normalize_phone_fr

log = logging.getLogger("fbdb.mobile_find")
BASE = os.environ.get("LUSHA_BASE", "https://api.lusha.com/v2/person")
AUTH_HEADER = os.environ.get("LUSHA_AUTH_HEADER", "api_key")
DAILY_CAP = int(os.environ.get("LUSHA_DAILY_CAP", "25"))
PROVIDER = "lusha"
KEEP_TYPES = ("mobile", "direct")           # a generic "Phone" is the company's number: never stored here


def _domain(value) -> Optional[str]:
    d = (str(value or "").strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]).removeprefix("www.")
    return d or None


class MobileProvider:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0, transport=None):
        self._key = api_key or os.environ["LUSHA_API_KEY"]
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(3), reraise=True)
    def find(self, first_name: str, last_name: str, domain: str) -> Optional[dict]:
        """{"phones": [{"number", "type", "do_not_call", "updated"}], "company_domain", "charged"} or None when the provider has no person."""
        r = self._http.get(BASE, params={"firstName": first_name, "lastName": last_name, "companyDomain": domain, "revealPhones": "true"},
                           headers={AUTH_HEADER: self._key})
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Lusha HTTP {r.status_code}: invalid key or no credits left")
        if r.status_code in (404, 204):
            return None
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        contact = (r.json() or {}).get("contact") or {}
        data = contact.get("data") or {}
        phones = []
        for p in data.get("phoneNumbers") or []:
            number = p.get("internationalNumber") or p.get("number") or p.get("phoneNumber") or p.get("localNumber")
            if number:
                phones.append({"number": str(number), "type": str(p.get("type") or p.get("phoneType") or "").strip().lower(),
                               "do_not_call": bool(p.get("doNotCall", p.get("dnc", False))), "updated": p.get("updateDate")})
        if not phones:
            return None
        return {"phones": phones, "company_domain": _domain((data.get("company") or {}).get("domain")), "charged": contact.get("isCreditCharged")}


SELECT_DUE = """
SELECT i.interlocutor_id, i.company_id, i.first_name, i.last_name, i.function_category, w.domain
FROM company_interlocutors i
JOIN companies c ON c.company_id = i.company_id AND c.status = 'active' AND c.is_diffusible
JOIN LATERAL (SELECT k.value_normalized AS domain FROM company_contacts k WHERE k.company_id = i.company_id
                AND k.contact_type = 'website' AND k.match_proof IS NOT NULL AND k.value_status IN ('verified','sourced')
              ORDER BY (k.value_status = 'verified') DESC, k.is_primary DESC, k.contact_id LIMIT 1) w ON TRUE
LEFT JOIN mobile_find_attempts a ON a.interlocutor_id = i.interlocutor_id
WHERE i.is_active AND coalesce(i.first_name, '') <> '' AND i.function_category = ANY(%(cats)s)
  AND NOT EXISTS (SELECT 1 FROM company_contacts k WHERE k.interlocutor_id = i.interlocutor_id AND k.contact_type = 'phone'
                  AND k.value_status <> 'unavailable' AND (k.value_normalized ~ '^\\+33[67]' OR k.line_type = 'mobile'))
  AND (a.searched_at IS NULL OR a.searched_at < now() - make_interval(days => %(days)s))
ORDER BY i.decision_maker_confirmed DESC, (i.function_category = 'director') DESC, a.searched_at NULLS FIRST, i.interlocutor_id
LIMIT %(limit)s
"""


def _remember(cur, interlocutor_id: int, domain: str, outcome: str) -> str:
    cur.execute(
        """INSERT INTO mobile_find_attempts (interlocutor_id, searched_at, domain, outcome) VALUES (%s, now(), %s, %s)
           ON CONFLICT (interlocutor_id) DO UPDATE SET searched_at = now(), domain = EXCLUDED.domain, outcome = EXCLUDED.outcome""",
        (interlocutor_id, domain, outcome))
    return outcome


def _opposed_everywhere(cur, number: str) -> bool:
    cur.execute("SELECT count(DISTINCT channel) FROM contact_oppositions WHERE contact_type = 'phone' AND value_normalized = %s "
                "AND channel IN ('call','sms')", (number,))
    return cur.fetchone()[0] >= 2


def store_found(cur, row: tuple, found: Optional[dict], source_id: int, contract_ref: str) -> str:
    """Apply one provider answer. Returns the outcome stored in mobile_find_attempts."""
    interlocutor_id, company_id, _first, _last, category, domain = row
    if not found:
        return _remember(cur, interlocutor_id, domain, "not_found")
    if found.get("company_domain") and found["company_domain"] != _domain(domain):
        return _remember(cur, interlocutor_id, domain, "other_company")          # the provider matched a person of another company
    candidates = [p for p in found["phones"] if p["type"] in KEEP_TYPES]
    if not candidates:
        return _remember(cur, interlocutor_id, domain, "no_mobile")
    candidates.sort(key=lambda p: KEEP_TYPES.index(p["type"]))                    # a mobile before a direct line
    saw = set()
    for p in candidates:
        number = normalize_phone_fr(p["number"])
        if not number:
            saw.add("invalid")
        elif p["do_not_call"]:
            saw.add("do_not_call")
        elif _opposed_everywhere(cur, number):
            saw.add("opposed")
        else:
            return _remember(cur, interlocutor_id, domain, _store_number(cur, company_id, interlocutor_id, category, number, p, domain,
                                                                      source_id, contract_ref))
    for outcome in ("do_not_call", "opposed", "invalid"):
        if outcome in saw:
            return _remember(cur, interlocutor_id, domain, outcome)
    return _remember(cur, interlocutor_id, domain, "not_found")


def _store_number(cur, company_id, interlocutor_id, category, number, p, domain, source_id, contract_ref) -> str:
    use = "direction" if category == "director" else None
    cur.execute("SELECT contact_id, interlocutor_id FROM company_contacts WHERE company_id = %s AND contact_type = 'phone' "
                "AND value_normalized = %s", (company_id, number))
    existing = cur.fetchone()
    if existing:
        if existing[1] is None:                    # the company already held it, nobody owned it: the provider attributes it to this person
            try:
                link_contact(cur, company_id, interlocutor_id, "phone", number, "provider_attributed", use)
            except ValueError as exc:
                log.info("not linked %s: %s", number, exc)
        return "already_known"
    proof = (f"Lusha person lookup (first name + last name + domain {domain}): number of type {p['type']}, provider update date "
             f"{p.get('updated') or 'unknown'}; contract {contract_ref}. Provider-attributed: not checked by us")
    cur.execute(
        """INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status, source_id, match_proof)
           VALUES (%s,'phone',%s,%s,FALSE,'sourced',%s,%s)""", (company_id, number, number, source_id, proof))
    link_contact(cur, company_id, interlocutor_id, "phone", number, "provider_attributed", use)
    return "found"


def run(dsn: str, client=None, limit: int = 25, categories=("director",), refresh_days: int = 180, cap: int = DAILY_CAP,
        contract_ref: Optional[str] = None) -> dict:
    contract_ref = contract_ref or os.environ.get("LUSHA_CONTRACT_REF")
    if not contract_ref:
        raise RuntimeError("licence gate: set LUSHA_CONTRACT_REF (or --contract-ref) to the reference of the signed contract before any "
                           "direct-mobile lookup (see docs/14-licensed-contacts.md)")
    bad = [c for c in categories if c not in CATEGORIES]
    if bad:
        raise ValueError(f"unknown categories {bad}: use {CATEGORIES}")
    client = client or MobileProvider()
    stats = {"read": 0, "found": 0, "not_found": 0, "no_mobile": 0, "do_not_call": 0, "opposed": 0, "invalid": 0, "other_company": 0,
             "already_known": 0, "errors": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code = 'lusha'").fetchone()[0]
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
                log.warning("mobile find failed for interlocutor %s: %s", row[0], exc)
                continue
            with conn.cursor() as cur:
                stats[store_found(cur, row, found, source_id, contract_ref)] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--categories", default="director", help="comma separated function categories")
    ap.add_argument("--refresh-days", type=int, default=180)
    ap.add_argument("--contract-ref", help="reference of the signed provider contract (or LUSHA_CONTRACT_REF)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, categories=tuple(c.strip() for c in a.categories.split(",") if c.strip()),
            refresh_days=a.refresh_days, contract_ref=a.contract_ref)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
