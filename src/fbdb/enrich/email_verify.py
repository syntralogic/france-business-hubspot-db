"""Email deliverability check (Hunter Email Verifier). PAID: one credit per check, hard DAILY cap.

Checks emails ALREADY in company_contacts (from the licensed file or a proven website). It never finds or guesses an address.
Hunter: GET https://api.hunter.io/v2/email-verifier?email=..&api_key=.., answer {"data": {"status": valid|invalid|accept_all|
webmail|disposable|unknown, "result": deliverable|undeliverable|risky}}. NOT verified against a live key from the build
sandbox (blocked): run `--limit 5` first. Cap: HUNTER_DAILY_CAP checks (default 50).

Result handling: 'valid' -> value_status 'verified'; 'invalid' or 'disposable' -> 'unavailable' (so the export drops it, the row is
kept as history); everything else (accept_all, webmail, unknown) -> status unchanged, verdict stored in verify_result.
Rechecked every 90 days. Usage: python -m fbdb.enrich.email_verify [--limit 50]. Needs DATABASE_URL, HUNTER_API_KEY.
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

log = logging.getLogger("fbdb.email_verify")
BASE = os.environ.get("HUNTER_BASE", "https://api.hunter.io/v2/email-verifier")
DAILY_CAP = int(os.environ.get("HUNTER_DAILY_CAP", "50"))
PROVIDER = "hunter"


class RetryableError(Exception):
    pass


class QuotaExhausted(Exception):
    """401/402/403/429-with-quota: bad key or no credits left."""


def decide(status: Optional[str]) -> tuple[Optional[str], str]:
    """(new value_status or None to leave unchanged, stored verdict)."""
    s = (status or "unknown").lower()
    if s == "valid":
        return "verified", s
    if s in ("invalid", "disposable"):
        return "unavailable", s
    return None, s


class HunterClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0, transport=None):
        self._key = api_key or os.environ["HUNTER_API_KEY"]
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(3), reraise=True)
    def verify(self, email: str) -> str:
        r = self._http.get(BASE, params={"email": email, "api_key": self._key})
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Hunter HTTP {r.status_code}: invalid key or no credits left")
        if r.status_code in (202, 222, 429) or r.status_code >= 500:   # 202/222: still verifying, retry later
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return (r.json().get("data") or {}).get("status") or "unknown"


SELECT_DUE = """
SELECT k.contact_id, k.value_normalized FROM company_contacts k JOIN companies c ON c.company_id = k.company_id
WHERE k.contact_type = 'email' AND k.value_status IN ('sourced','verified') AND k.opposed_at IS NULL
  AND NOT (EXISTS (SELECT 1 FROM contact_oppositions o WHERE o.contact_type = 'email' AND o.value_normalized = k.value_normalized AND o.channel = 'email'))
  AND c.status = 'active' AND c.is_diffusible
  AND (k.checked_at IS NULL OR k.checked_at < now() - make_interval(days => %(days)s))
ORDER BY k.checked_at NULLS FIRST, k.contact_id LIMIT %(limit)s
"""


def run(dsn: str, client=None, limit: int = 50, refresh_days: int = 90, cap: int = DAILY_CAP) -> dict:
    client = client or HunterClient()
    stats = {"read": 0, "verified": 0, "unavailable": 0, "unchanged": 0, "errors": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        for contact_id, email in conn.execute(SELECT_DUE, {"days": refresh_days, "limit": limit}).fetchall():
            if not usage.reserve(conn, PROVIDER, cap, 1):
                stats["cap_reached"] = True
                break
            stats["read"] += 1
            try:
                verdict = client.verify(email)
            except QuotaExhausted as exc:
                usage.release(conn, PROVIDER, 1)
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                usage.release(conn, PROVIDER, 1)
                stats["errors"] += 1
                log.warning("verify failed for contact %s: %s", contact_id, exc)
                continue
            new_status, stored = decide(verdict)
            conn.execute(
                """UPDATE company_contacts SET verify_result=%s, checked_at=now(),
                     value_status=COALESCE(%s, value_status),
                     last_verified_at=CASE WHEN %s='verified' THEN now() ELSE last_verified_at END
                   WHERE contact_id=%s""", (stored, new_status, new_status or "", contact_id))
            conn.commit()
            stats[new_status or "unchanged"] += 1
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--refresh-days", type=int, default=90)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
