"""Pappers financials (revenue, headcount) by SIREN. PAID: needs a Pappers API plan and key.

API (Pappers v2): GET https://api.pappers.fr/v2/entreprise?api_token=KEY&siren=NNNNNNNNN, JSON with a `finances` list
(annee, date_cloture_exercice, duree_exercice, chiffre_affaires, resultat, effectif, and the balance-sheet figures capitaux_propres /
tresorerie / endettement under tolerant aliases, see BALANCE_ALIASES: a figure Pappers does not publish stays NULL, never 0). Pappers sells credits (subscription or pay-as-you-go) and the API stops
when they run out. NOT verified against a live key from the build sandbox (blocked): field names are tolerant, the credit
cost per call is configurable (PAPPERS_CREDITS_PER_CALL, default 1: check the plan, some options cost more), and the
first run must be `--limit 5` with the log read. Confirm in the contract that storing results in a CRM is allowed.

Spend control: every call goes through fbdb.common.usage.reserve() with a hard DAILY credit cap (PAPPERS_DAILY_CAP,
default 200 credits). When the cap is reached the run stops cleanly and the rest waits for the next day.
Per EXERCISE (spec section 4): each row keeps its closing date and length in months when Pappers gives them; a 6 or 18 month
exercise is stored as such and never presented as an annual revenue (the HubSpot view only uses 12-month or unknown-length
exercises). A figure that differs from the one already stored goes to field_conflicts (`financials:<year>:<field>`), the first
value is kept. Search status per company (financials_checks): found / not_found (provider has no accounts: NOT "no revenue")
/ inaccessible (call failed, retried next run); no row = not searched. Field names date_cloture_exercice / duree_exercice are
from the Pappers documentation and NOT verified live.
Selection: active, diffusible companies that are not sole proprietors (they are the ones that file public accounts), never
checked first, then oldest check; re-checked every 180 days (accounts change once a year). Only exercises with a revenue, a result or a
headcount are stored (company_financials, latest 3 years); nothing is invented for companies that publish no accounts.

Usage: python -m fbdb.enrich.pappers [--limit 200] [--refresh-days 180]
Needs: DATABASE_URL, PAPPERS_API_KEY. Env: PAPPERS_DAILY_CAP, PAPPERS_CREDITS_PER_CALL, PAPPERS_BASE.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import date
from typing import Optional

import httpx
import psycopg
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fbdb.common import usage
from fbdb.common.provenance import record_conflict

log = logging.getLogger("fbdb.pappers")
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx INFO logs print the full URL, which carries api_token=KEY
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


def _date(v) -> Optional[date]:
    """ISO (2023-12-31) or French (31/12/2023) date; None when absent or unreadable (never guessed)."""
    if not v:
        return None
    t = str(v).strip()[:10]
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t) or None
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", t)
        return date(int(m[3]), int(m[2]), int(m[1])) if m else None
    except ValueError:
        return None


def _months(v) -> Optional[int]:
    n = _num(v)
    return int(n) if n is not None and 1 <= n <= 36 and n == int(n) else None


# Balance-sheet fields (spec section 4). Tolerant aliases, NOT verified against a live key: the first alias present wins.
BALANCE_ALIASES = {
    "equity": ("capitaux_propres", "fonds_propres"),
    "cash": ("tresorerie", "disponibilites"),
    "debt": ("endettement", "dettes_financieres"),
}


def _first_num(f: dict, names) -> Optional[float]:
    for n in names:
        v = _num(f.get(n))
        if v is not None:
            return v
    return None


def parse_finances(payload: dict, keep: int = 3) -> list[dict]:
    """[{year, period_end, duration_months, revenue, net_result, employees, equity, cash, debt}] newest first.

    One row per exercise; only rows with a revenue, a result or a headcount. The same (year, closing date) twice keeps the first."""
    rows, seen = [], set()
    for f in payload.get("finances") or []:
        try:
            year = int(f.get("annee"))
        except (TypeError, ValueError):
            continue
        rev, res, emp = _num(f.get("chiffre_affaires")), _num(f.get("resultat")), _num(f.get("effectif"))
        bal = {k: _first_num(f, names) for k, names in BALANCE_ALIASES.items()}
        if rev is None and res is None and emp is None and all(v is None for v in bal.values()):
            continue
        end = _date(f.get("date_cloture_exercice"))
        if (year, end) in seen:
            continue
        seen.add((year, end))
        rows.append({"year": year, "period_end": end, "duration_months": _months(f.get("duree_exercice")),
                     "revenue": rev, "net_result": res, "employees": int(emp) if emp is not None else None, **bal})
    rows.sort(key=lambda r: (r["period_end"] or date(r["year"], 12, 31)), reverse=True)
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


FIELDS = (("revenue", "revenue_eur"), ("net_result", "net_result_eur"), ("employees", "employees"),
          ("equity", "equity_eur"), ("cash", "cash_eur"), ("debt", "debt_eur"))


def store(conn, company_id: int, rows: list[dict], source_id: int, source_url: Optional[str] = None) -> dict:
    """Inserts new exercises; an exercise already stored keeps its values, a different figure goes to field_conflicts and
    a figure we did not have yet is filled in. Returns {"stored", "conflicts"}."""
    stored = conflicts = 0
    for r in rows:
        cur = conn.execute(
            """SELECT financial_id, revenue_eur, net_result_eur, employees, equity_eur, cash_eur, debt_eur FROM company_financials
               WHERE company_id=%s AND source_id=%s AND fiscal_year=%s AND period_end IS NOT DISTINCT FROM %s""",
            (company_id, source_id, r["year"], r["period_end"])).fetchone()
        if cur is None:
            conn.execute(
                """INSERT INTO company_financials (company_id, fiscal_year, period_end, duration_months, revenue_eur,
                     net_result_eur, employees, equity_eur, cash_eur, debt_eur, source_id, source_url)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (company_id, r["year"], r["period_end"], r["duration_months"], r["revenue"], r["net_result"],
                 r["employees"], r.get("equity"), r.get("cash"), r.get("debt"), source_id, source_url))
            stored += 1
            continue
        fid, have = cur[0], dict(zip(("revenue", "net_result", "employees", "equity", "cash", "debt"), cur[1:]))
        for key, col in FIELDS:
            new, old = r.get(key), have[key]
            if new is None:
                continue
            if old is None:
                conn.execute(f"UPDATE company_financials SET {col}=%s WHERE financial_id=%s", (new, fid))
            elif float(old) != float(new):
                label = r["period_end"].isoformat() if r["period_end"] else str(r["year"])
                fmt = (lambda v: str(int(v))) if col == "employees" else (lambda v: f"{float(v):.2f}")
                record_conflict(conn, company_id, f"financials:{label}:{col}", fmt(old), source_id, fmt(new), source_id)
                conflicts += 1
        conn.execute("UPDATE company_financials SET collected_at=now(), duration_months=COALESCE(duration_months, %s) "
                     "WHERE financial_id=%s", (r["duration_months"], fid))
    return {"stored": stored, "conflicts": conflicts}


def set_status(conn, company_id: int, status: str, detail: str, source_id: int, exercises: int = 0) -> None:
    conn.execute(
        """INSERT INTO financials_checks (company_id, status, detail, exercises_found, source_id) VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (company_id) DO UPDATE SET status=EXCLUDED.status, detail=EXCLUDED.detail,
             exercises_found=EXCLUDED.exercises_found, source_id=EXCLUDED.source_id, checked_at=now()""",
        (company_id, status, detail, exercises, source_id))


def run(dsn: str, client=None, limit: int = 200, refresh_days: int = 180, cap: int = DAILY_CAP,
        credits_per_call: int = CREDITS_PER_CALL) -> dict:
    client = client or PappersClient()
    stats = {"read": 0, "with_financials": 0, "no_accounts": 0, "not_found": 0, "errors": 0,
             "credits_used": 0, "conflicts": 0, "cap_reached": False, "stopped_no_credits": False}
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
                set_status(conn, company_id, "inaccessible", str(exc)[:200], source_id)
                conn.commit()
                log.warning("Pappers lookup failed for %s: %s", siren, exc)
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    log.error("%d consecutive failures, aborting", consecutive)
                    break
                continue
            stats["credits_used"] += credits_per_call
            if payload is None:
                stats["not_found"] += 1
                conn.execute("UPDATE companies SET financials_checked_at=now() WHERE company_id=%s", (company_id,))
                set_status(conn, company_id, "not_found", "company unknown to the provider", source_id)
            else:
                rows = parse_finances(payload)
                res = store(conn, company_id, rows, source_id, f"{BASE}?siren={siren}")
                conn.execute("UPDATE companies SET financials_checked_at=now() WHERE company_id=%s", (company_id,))
                stats["conflicts"] += res["conflicts"]
                if rows:
                    set_status(conn, company_id, "found", "", source_id, len(rows))
                else:
                    set_status(conn, company_id, "not_found", "no accounts in the provider data (not proof of none)", source_id)
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
