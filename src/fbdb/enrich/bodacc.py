"""BODACC announcements (collective proceedings, radiations) by SIREN.

Source: DILA open dataset `annonces-commerciales` on the OpenDataSoft portal (free, Licence Ouverte, no key, daily):
https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records
OpenDataSoft refuses offset pagination beyond 10,000 rows per query, so the pull is split into one query per
day and per family (each far below the cap).

What it does: stores each announcement in bodacc_events (idempotent on the announcement id) and, for SIRENs that
exist in our base, opens one data_quality_issues row per announcement (BODACC_COLLECTIVE_PROCEDURE = warning,
BODACC_RADIATION = info) so a person can review before anything is changed. It never edits company status.
Unverified against the live API from the build sandbox (blocked): field names and family codes are configurable
and the parser is tolerant; run `python -m fbdb.enrich.bodacc --days 3` once on the server and read the log.

Usage: python -m fbdb.enrich.bodacc [--days 8] [--families collective,radiation]
Needs: DATABASE_URL. Env: BODACC_BASE, BODACC_FAMILIES.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Callable, Iterator, Optional

import httpx
import psycopg
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger("fbdb.bodacc")

BASE = os.environ.get(
    "BODACC_BASE",
    "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records",
)
DEFAULT_FAMILIES = os.environ.get("BODACC_FAMILIES", "collective,radiation")
PAGE = 100
OFFSET_CAP = 10000
ISSUE = {"collective": ("BODACC_COLLECTIVE_PROCEDURE", "warning"), "radiation": ("BODACC_RADIATION", "info")}
# any other family (creation, modification...) is stored but raises no data-quality issue: it feeds register_events instead


class RetryableError(Exception):
    pass


def parse_siren(registre) -> str:
    """`registre` is a list like ['552 100 554', '552100554'] (or a string). Return the first valid 9-digit SIREN or ''."""
    items = registre if isinstance(registre, list) else [registre or ""]
    for it in items:
        d = re.sub(r"\D", "", str(it))
        if len(d) == 9:
            return d
    return ""


def parse_record(rec: dict) -> Optional[dict]:
    """Normalize one API record; None when it has no usable id, SIREN or date."""
    siren = parse_siren(rec.get("registre"))
    bid = (rec.get("id") or "").strip()
    try:
        pub = datetime.strptime((rec.get("dateparution") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    if not (siren and bid):
        return None
    return {
        "bodacc_id": bid, "siren": siren, "family": (rec.get("familleavis") or "").strip().lower(),
        "family_label": rec.get("familleavis_lib"), "avis_type": rec.get("typeavis"), "published_on": pub,
        "court": rec.get("tribunal"), "department": rec.get("numerodepartement"), "url": rec.get("url_complete"),
    }


@retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
       wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(5), reraise=True)
def _get(params: dict, client: Optional[httpx.Client] = None) -> dict:
    c = client or httpx
    resp = c.get(BASE, params=params, timeout=60.0)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def fetch_day(day: date, family: str, get: Callable[[dict], dict] = _get) -> Iterator[dict]:
    """Yield raw records for one publication day and family, paging by offset (always under the 10k cap)."""
    where = f"dateparution = date'{day.isoformat()}' AND familleavis = '{family}'"
    offset = 0
    while offset < OFFSET_CAP:
        data = get({"where": where, "limit": PAGE, "offset": offset, "order_by": "id"})
        rows = data.get("results") or []
        yield from rows
        total = data.get("total_count", 0)
        offset += PAGE
        if not rows or offset >= total:
            return
    log.error("BODACC %s %s hit the %d offset cap: some records not fetched", day, family, OFFSET_CAP)


def run(dsn: str, days: int = 8, families: str = DEFAULT_FAMILIES, today: Optional[date] = None,
        get: Callable[[dict], dict] = _get) -> dict:
    today = today or date.today()
    fams = [f.strip() for f in families.split(",") if f.strip()]
    stats = {"fetched": 0, "skipped": 0, "stored": 0, "issues": 0, "request_errors": 0}
    events: dict[str, dict] = {}
    for i in range(days):
        day = today - timedelta(days=i)
        for fam in fams:
            try:
                for rec in fetch_day(day, fam, get):
                    stats["fetched"] += 1
                    ev = parse_record(rec)
                    if ev:
                        events[ev["bodacc_id"]] = ev
                    else:
                        stats["skipped"] += 1
            except (RetryableError, httpx.HTTPError) as e:
                stats["request_errors"] += 1
                log.error("BODACC %s %s failed after retries: %s", day, fam, e)
    with psycopg.connect(dsn) as conn:
        src = conn.execute("SELECT source_id FROM data_sources WHERE code='bodacc'").fetchone()[0]
        for ev in events.values():
            cur = conn.execute(
                """INSERT INTO bodacc_events(bodacc_id,siren,family,family_label,avis_type,published_on,court,department,url)
                   VALUES (%(bodacc_id)s,%(siren)s,%(family)s,%(family_label)s,%(avis_type)s,%(published_on)s,%(court)s,%(department)s,%(url)s)
                   ON CONFLICT (bodacc_id) DO NOTHING""", ev)
            if cur.rowcount == 0:
                continue
            stats["stored"] += 1
            if ev["family"] not in ISSUE:
                continue
            code, sev = ISSUE[ev["family"]]
            cur = conn.execute(
                """INSERT INTO data_quality_issues(company_id, issue_code, severity, field_name, details)
                   SELECT company_id, %s, %s, 'status', %s FROM companies WHERE siren=%s""",
                (code, sev, f"{ev['bodacc_id']} {ev['published_on']} {ev['family_label'] or ev['family']}: {ev['url'] or ''}".strip(),
                 ev["siren"]))
            stats["issues"] += cur.rowcount
        conn.commit()
    log.info("BODACC %s (source %s)", stats, src)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--families", default=DEFAULT_FAMILIES)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], a.days, a.families)
    print(s)
    if s["request_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
