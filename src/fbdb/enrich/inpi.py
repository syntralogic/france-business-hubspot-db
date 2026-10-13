"""INPI RNE (Registre National des Entreprises) cross-check by SIREN.

Source: INPI RNE API (free account at registre-national-entreprises.inpi.fr; login with username/password gives a bearer token):
  POST https://registre-national-entreprises.inpi.fr/api/sso/login   {"username":..., "password":...} -> {"token": ...}
  GET  https://registre-national-entreprises.inpi.fr/api/companies/{siren}   (Authorization: Bearer ...)
Daily data; quotas and terms are set by INPI: check them in your account and lower INPI_PER_RUN / raise INPI_MIN_INTERVAL_S if needed.

Role in this pipeline: SECOND OPINION, never overwrite. SIRENE stays the authority for status and the Annuaire
loader for directors. For a bounded number of companies per run (never checked first, oldest check first) it
  * flags INPI_RADIATION_PRESENT when INPI shows a radiation/cessation date but we still hold the company as active,
  * flags INPI_DIRECTOR_MISMATCH when INPI's directors' family names differ from our active directors,
and opens one data_quality_issues row per company and code while one is still open. Companies are re-checked every 60 days.
The JSON layout was read from INPI's technical documentation, not from a live response (sandbox blocked): the
extractors are tolerant, and the first real run must be checked (`--limit 5`, then read the log and the issues).

Usage: python -m fbdb.enrich.inpi [--limit 500] [--refresh-days 60]
Needs: DATABASE_URL, INPI_USERNAME, INPI_PASSWORD. Env: INPI_BASE, INPI_MIN_INTERVAL_S.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
import unicodedata
from typing import Any, Iterator, Optional

import httpx
import psycopg
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger("fbdb.inpi")
BASE = os.environ.get("INPI_BASE", "https://registre-national-entreprises.inpi.fr/api")
MIN_INTERVAL_S = float(os.environ.get("INPI_MIN_INTERVAL_S", "1.0"))
MAX_CONSECUTIVE_ERRORS = 10


class RetryableError(Exception):
    pass


def _norm(s: Any) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _walk(o: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def extract_director_surnames(payload: dict) -> set[str]:
    """Normalized family names of natural-person directors (items of any `pouvoirs` list)."""
    out: set[str] = set()
    for k, v in _walk(payload):
        if k != "pouvoirs" or not isinstance(v, list):
            continue
        for p in v:
            ind = (p or {}).get("individu") or {}
            d = ind.get("descriptionPersonne") or ind.get("descPersonne") or ind
            n = _norm(d.get("nom") or d.get("nomNaissance"))
            if n:
                out.add(n)
    return out


def extract_radiation_date(payload: dict) -> Optional[str]:
    """First non-empty radiation/cessation date found anywhere in the notice, else None."""
    for k, v in _walk(payload):
        kl = k.lower()
        if ("dateradiation" in kl or "datecessation" in kl) and isinstance(v, str) and v.strip():
            return v.strip()[:10]
    return None


class InpiClient:
    def __init__(self, username: Optional[str] = None, password: Optional[str] = None, timeout: float = 60.0):
        self._user = username or os.environ["INPI_USERNAME"]
        self._pw = password or os.environ["INPI_PASSWORD"]
        self._http = httpx.Client(timeout=timeout, headers={"Accept": "application/json"})
        self._token: Optional[str] = None
        self._last = 0.0

    def _login(self) -> None:
        r = self._http.post(f"{BASE}/sso/login", json={"username": self._user, "password": self._pw})
        r.raise_for_status()
        self._token = r.json()["token"]

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(5), reraise=True)
    def get_company(self, siren: str) -> Optional[dict]:
        """Notice for one SIREN, or None when INPI does not know it."""
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in (1, 2):
            if not self._token:
                self._login()
            r = self._http.get(f"{BASE}/companies/{siren}", headers={"Authorization": f"Bearer {self._token}"})
            self._last = time.monotonic()
            if r.status_code == 401 and attempt == 1:  # expired token: log in again once
                self._token = None
                continue
            break
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()


SELECT_DUE = """
SELECT company_id, siren, status FROM companies
WHERE status = 'active' AND is_diffusible AND NOT is_sole_proprietor
  AND (inpi_checked_at IS NULL OR inpi_checked_at < now() - make_interval(days => %(days)s))
ORDER BY inpi_checked_at NULLS FIRST, company_id
LIMIT %(limit)s
"""


def _flag(cur, company_id: int, code: str, details: str, batch_id: int) -> int:
    cur.execute(
        """INSERT INTO data_quality_issues(company_id, batch_id, issue_code, severity, field_name, details)
           SELECT %s, %s, %s, 'warning', %s, %s
           WHERE NOT EXISTS (SELECT 1 FROM data_quality_issues WHERE company_id=%s AND issue_code=%s AND status='open')""",
        (company_id, batch_id, code, "status" if "RADIATION" in code else "directors", details, company_id, code))
    return cur.rowcount


def check_company(cur, company_id: int, notice: dict, batch_id: int) -> dict:
    res = {"radiation": 0, "mismatch": 0}
    rad = extract_radiation_date(notice)
    if rad:
        res["radiation"] = _flag(cur, company_id, "INPI_RADIATION_PRESENT", f"INPI shows a radiation/cessation date {rad}; SIRENE still active", batch_id)
    theirs = extract_director_surnames(notice)
    cur.execute("SELECT last_name FROM company_directors WHERE company_id=%s AND is_active", (company_id,))
    ours = {_norm(r[0]) for r in cur.fetchall() if r[0]}
    if theirs and ours and theirs != ours:
        res["mismatch"] = _flag(cur, company_id, "INPI_DIRECTOR_MISMATCH",
                                f"INPI lists {len(theirs)} director family name(s), we hold {len(ours)}; {len(theirs & ours)} in common", batch_id)
    cur.execute("UPDATE companies SET inpi_checked_at = now() WHERE company_id = %s", (company_id,))
    return res


def run(dsn: str, client=None, limit: int = 500, refresh_days: int = 60) -> dict:
    client = client or InpiClient()
    stats = {"read": 0, "radiation_flags": 0, "director_flags": 0, "not_found": 0, "errors": 0}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_id FROM data_sources WHERE code='inpi_rne'")
        source_id = cur.fetchone()[0]
        cur.execute("INSERT INTO import_export_batches (batch_type, source_id) VALUES ('enrich', %s) RETURNING batch_id", (source_id,))
        batch_id = cur.fetchone()[0]
        conn.commit()
        cur.execute(SELECT_DUE, {"days": refresh_days, "limit": limit})
        due = cur.fetchall()
        consecutive, status, note = 0, "success", None
        try:
            for i, (company_id, siren, _st) in enumerate(due, 1):
                stats["read"] += 1
                try:
                    notice = client.get_company(siren)
                    consecutive = 0
                except Exception as exc:
                    stats["errors"] += 1
                    consecutive += 1
                    log.warning("INPI lookup failed for %s: %s", siren, exc)
                    if consecutive >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(f"{consecutive} consecutive INPI failures, aborting") from exc
                    continue
                if notice is None:
                    stats["not_found"] += 1
                    cur.execute("UPDATE companies SET inpi_checked_at = now() WHERE company_id=%s", (company_id,))
                    continue
                r = check_company(cur, company_id, notice, batch_id)
                stats["radiation_flags"] += r["radiation"]
                stats["director_flags"] += r["mismatch"]
                if i % 100 == 0:
                    conn.commit()
            if stats["errors"]:
                status, note = "partial", f"{stats['errors']} lookups failed and will be retried next run"
        except Exception as exc:
            status, note = "failed", str(exc)[:1000]
            raise
        finally:
            conn.commit()
            cur.execute(
                "UPDATE import_export_batches SET status=%s, finished_at=now(), rows_read=%s, rows_review=%s, notes=%s WHERE batch_id=%s",
                (status, stats["read"], stats["radiation_flags"] + stats["director_flags"], note, batch_id))
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--refresh-days", type=int, default=60)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days))


if __name__ == "__main__":
    main()
