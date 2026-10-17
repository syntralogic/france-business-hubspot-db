"""Phone line type (landline / mobile / voip) via Twilio Lookup v2. PAID: per-lookup price, hard DAILY cap.

Checks phones ALREADY in company_contacts. The line type matters for outreach rules (SMS only to mobiles, and French
prospecting rules differ per channel); it is stored in company_contacts.line_type and does not change value_status.
Twilio: GET https://lookups.twilio.com/v2/PhoneNumbers/+33...?Fields=line_type_intelligence, basic auth (account SID, auth token),
answer {"valid": bool, "line_type_intelligence": {"type": "landline"|"mobile"|"fixedVoip"|"nonFixedVoip"|...}}. Line type
intelligence is a paid add-on: check the price and availability for France in the Twilio console. NOT verified against a live
account from the build sandbox (blocked): run `--limit 5` first. Cap: TWILIO_DAILY_CAP lookups (default 100).

An invalid number (valid=false) is marked 'unavailable' so the export drops it. Rechecked every 365 days.
Usage: python -m fbdb.enrich.phone_lookup [--limit 100]. Needs DATABASE_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN.
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

log = logging.getLogger("fbdb.phone_lookup")
BASE = os.environ.get("TWILIO_LOOKUP_BASE", "https://lookups.twilio.com/v2/PhoneNumbers")
DAILY_CAP = int(os.environ.get("TWILIO_DAILY_CAP", "100"))
PROVIDER = "twilio_lookup"
LINE_TYPES = {"landline": "landline", "mobile": "mobile", "fixedvoip": "voip", "nonfixedvoip": "voip", "voip": "voip",
              "tollfree": "tollfree", "premium": "premium", "sharedcost": "sharedcost", "personal": "personal"}


class RetryableError(Exception):
    pass


class QuotaExhausted(Exception):
    """401/402/403: bad credentials or account without credit / without the add-on."""


def parse_lookup(payload: dict) -> tuple[bool, Optional[str]]:
    """(is_valid, line_type or None). Unknown types are stored as 'other', never guessed."""
    valid = bool(payload.get("valid"))
    raw = ((payload.get("line_type_intelligence") or {}).get("type") or "").replace("_", "").lower()
    return valid, (LINE_TYPES.get(raw, "other") if raw else None)


class TwilioLookupClient:
    def __init__(self, sid: Optional[str] = None, token: Optional[str] = None, timeout: float = 30.0, transport=None):
        self._auth = (sid or os.environ["TWILIO_ACCOUNT_SID"], token or os.environ["TWILIO_AUTH_TOKEN"])
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(3), reraise=True)
    def lookup(self, e164: str) -> Optional[dict]:
        r = self._http.get(f"{BASE}/{e164}", params={"Fields": "line_type_intelligence"}, auth=self._auth)
        if r.status_code == 404:
            return {"valid": False}
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Twilio HTTP {r.status_code}: invalid credentials, no credit, or add-on not enabled")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()


SELECT_DUE = """
SELECT k.contact_id, k.value_normalized FROM company_contacts k JOIN companies c ON c.company_id = k.company_id
WHERE k.contact_type = 'phone' AND k.value_status IN ('sourced','verified') AND k.opposed_at IS NULL
  AND c.status = 'active' AND c.is_diffusible
  AND (k.checked_at IS NULL OR k.checked_at < now() - make_interval(days => %(days)s))
ORDER BY k.checked_at NULLS FIRST, k.contact_id LIMIT %(limit)s
"""


def run(dsn: str, client=None, limit: int = 100, refresh_days: int = 365, cap: int = DAILY_CAP) -> dict:
    client = client or TwilioLookupClient()
    stats = {"read": 0, "mobile": 0, "landline": 0, "other_type": 0, "invalid": 0, "errors": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        for contact_id, phone in conn.execute(SELECT_DUE, {"days": refresh_days, "limit": limit}).fetchall():
            if not usage.reserve(conn, PROVIDER, cap, 1):
                stats["cap_reached"] = True
                break
            stats["read"] += 1
            try:
                valid, line_type = parse_lookup(client.lookup(phone) or {})
            except QuotaExhausted as exc:
                usage.release(conn, PROVIDER, 1)
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                usage.release(conn, PROVIDER, 1)
                stats["errors"] += 1
                log.warning("lookup failed for contact %s: %s", contact_id, exc)
                continue
            conn.execute(
                """UPDATE company_contacts SET line_type=%s, checked_at=now(),
                     value_status=CASE WHEN %s THEN value_status ELSE 'unavailable' END WHERE contact_id=%s""",
                (line_type, valid, contact_id))
            conn.commit()
            stats["invalid" if not valid else line_type if line_type in ("mobile", "landline") else "other_type"] += 1
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--refresh-days", type=int, default=365)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
