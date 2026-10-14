"""Pappers financials (revenue, headcount) by SIREN. PAID: needs a Pappers API plan and key.

API (Pappers v2): GET https://api.pappers.fr/v2/entreprise?api_token=KEY&siren=NNNNNNNNN, JSON with a `finances` list
(annee, chiffre_affaires, resultat, effectif). Pappers sells credits (subscription or pay-as-you-go) and the API stops
when they run out. NOT verified against a live key from the build sandbox (blocked): field names are tolerant, the credit
cost per call is configurable (PAPPERS_CREDITS_PER_CALL, default 1: check the plan, some options cost more), and the
first run must be `--limit 5` with the log read. Confirm in the contract that storing results in a CRM is allowed.

Spend control: every call goes through fbdb.common.usage.reserve() with a hard DAILY credit cap (PAPPERS_DAILY_CAP,
default 200 credits). When the cap is reached the run stops cleanly and the rest waits for the next day.
Selection: active, diffusible companies that are not sole proprietors (they are the ones that file public accounts), never
checked first, then oldest check; re-checked every 180 days (accounts change once a year). Only years with a revenue or
headcount are stored (company_financials, latest 3 years); nothing is invented for companies that publish no accounts.

Usage: python -m fbdb.enrich.pappers [--limit 200] [--refresh-days 180]
Needs: DATABASE_URL, PAPPERS_API_KEY. Env: PAPPERS_DAILY_CAP, PAPPERS_CREDITS_PER_CALL, PAPPERS_BASE.
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

log = logging.getLogger("fbdb.pappers")
BASE = os.environ.get("PAPPERS_BASE", "https://api.pappers.fr/v2/entreprise")
DAILY_CAP = int(os.environ.get("PAPPERS_DAILY_CAP", "200"))
CREDITS_PER_CALL = int(os.environ.get("PAPPERS_CREDITS_PER_CALL", "1"))
MAX_CONSECUTIVE_ERRORS = 10
PROVIDER = "pappers"


class RetryableError(Exception):
    pass


class QuotaExhausted(Exception):
    """Pappers answered 401/402/403/429-style 'no more credits / not allowed': stop, do not retry."""


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_finances(payload: dict, keep: int = 3) -> list[dict]:
    """[{year, revenue, net_result, employees}] newest first, only rows with revenue or employees."""
    rows = []
    for f in payload.get("finances") or []:
        try:
            year = int(f.get("annee"))
        except (TypeError, ValueError):
            continue
        rev, res = _num(f.get("chiffre_affaires")), _num(f.get("resultat"))
        emp = _num(f.get("effectif"))
        if rev is None and emp is None:
            continue
        rows.append({"year": year, "revenue": rev, "net_result": res, "employees": int(emp) if emp is not None else None})
    rows.sort(key=lambda r: -r["year"])
    return rows[:keep]


class PappersClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0):
        self._key = api_key or os.environ["PAPPERS_API_KEY"]
        self._http = httpx.Client(timeout=timeout)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(4), reraise=True)
    def get_company(self, siren: str) -> Optional[dict]:
        r = self._http.get(BASE, params={"api_token": self._key, "siren": siren})
        if r.status_code == 404:
            return None
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Pappers HTTP {r.status_code}: invalid key, no credits left, or not allowed")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()


SELECT_DUE = """
SELECT company_id, siren FROM companies
WHERE status='active' AND is_diffusible AND NOT is_sole_proprietor
  AND (financials_checked_at IS NULL OR financials_checked_at < now() - make_interval(days => %(days)s))
ORDER BY financials_checked_at NULLS FIRST, company_id
LIMIT %(limit)s
"""


def store(cur, company_id: int, rows: list[dict], source_id: int) -> int:
    for r in rows:
        cur.execute(
            """INSERT INTO company_financials (company_id, fiscal_year, revenue_eur, net_result_eur, employees, source_id)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, fiscal_year, source_id) DO UPDATE SET revenue_eur=EXCLUDED.revenue_eur,
                 net_result_eur=EXCLUDED.net_result_eur, employees=EXCLUDED.employees, collected_at=now()""",
            (company_id, r["year"], r["revenue"], r["net_result"], r["employees"], source_id))
    cur.execute("UPDATE companies SET financials_checked_at=now() WHERE company_id=%s", (company_id,))
    return len(rows)


def run(dsn: str, client=None, limit: int = 200, refresh_days: int = 180, cap: int = DAILY_CAP,
        credits_per_call: int = CREDITS_PER_CALL) -> dict:
    client = client or PappersClient()
    stats = {"read": 0, "with_financials": 0, "no_accounts": 0, "not_found": 0, "errors": 0,
             "credits_used": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code='pappers'").fetchone()[0]
        due = conn.execute(SELECT_DUE, {"days": refresh_days, "limit": limit}).fetchall()
        consecutive = 0
        for company_id, siren in due:
            if not usage.reserve(conn, PROVIDER, cap, credits_per_call):
                stats["cap_reached"] = True
                log.warning("Pappers daily cap of %d credits reached; stopping", cap)
                break
            stats["read"] += 1
            try:
                payload = client.get_company(siren)
                consecutive = 0
            except QuotaExhausted as exc:
                usage.release(conn, PROVIDER, credits_per_call)
                stats["stopped_no_credits"] = True
                log.error("%s", exc)
                break
            except Exception as exc:
                usage.release(conn, PROVIDER, credits_per_call)  # failed before a charge: give the credit back
                stats["errors"] += 1
                consecutive += 1
                log.warning("Pappers lookup failed for %s: %s", siren, exc)
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    log.error("%d consecutive failures, aborting", consecutive)
                    break
                continue
            stats["credits_used"] += credits_per_call
            if payload is None:
                stats["not_found"] += 1
                conn.execute("UPDATE companies SET financials_checked_at=now() WHERE company_id=%s", (company_id,))
            else:
                rows = parse_finances(payload)
                with conn.cursor() as cur:
                    store(cur, company_id, rows, source_id)
                stats["with_financials" if rows else "no_accounts"] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--refresh-days", type=int, default=180)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
