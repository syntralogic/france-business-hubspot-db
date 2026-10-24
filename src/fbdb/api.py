"""Read-only data API for the demo viewer (served by fbdb.web under /api).

Safe by design for a PUBLIC browser frontend:
  * read-only transaction and a statement timeout on every query;
  * only business fields are exposed: no phone, no email, no LinkedIn, no director names, no Google data;
  * sole proprietors (natural persons trading in their own name) are excluded, non-diffusible units never appear
    (the export view already filters them);
  * page size is capped, search text is bound as a parameter (never concatenated into SQL).

Endpoints (GET, JSON):
  /api/stats                         counts for the dashboard header
  /api/companies?q=&dept=&city=&rge=1&page=1&per_page=25
        q    : company name fragment, or a 9-digit SIREN / 14-digit SIRET
        dept : department code (e.g. 75, 2A)     city : city name fragment     rge=1 : only RGE-certified
  /api/company?siren=NNNNNNNNN       detail of one company: financial exercises (with balance figures), certificates (link, and only
                                     WHETHER an attestation document exists, never the file), activities and declarations read from
                                     the site (each with its page URL), legal relations, register events (agency opening, activity)
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import psycopg
from psycopg.rows import dict_row

MAX_PER_PAGE = 50
STATEMENT_TIMEOUT_MS = 8000
COLUMNS = (
    "v.siret, v.siren, v.legal_name, v.trading_name, v.establishment_name, v.street, v.postal_code, v.city, "
    "v.department_code, v.department_name, v.region, v.naf, v.naf_label, v.trade, v.legal_form, v.is_head_office, "
    "v.status, v.creation_date, v.employee_range, v.size_category, v.certifications, v.rge_labels, v.rge_until, "
    "v.website, v.revenue, v.revenue_year"
)
FROM = "FROM v_hubspot_companies v JOIN companies c ON c.siren = v.siren WHERE NOT c.is_sole_proprietor AND v.status = 'active'"


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_query(params: dict) -> tuple[str, list, int, int]:
    """Query-string dict -> (where fragment, bound values, page, per_page). Pure and unit-tested."""
    def one(key: str) -> str:
        v = params.get(key)
        v = v[0] if isinstance(v, list) else v
        return (v or "").strip()

    where, values = "", []
    q = one("q")
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        if q.replace(" ", "").isdigit() and len(digits) == 14:
            where += " AND v.siret = %s"; values.append(digits)
        elif q.replace(" ", "").isdigit() and len(digits) == 9:
            where += " AND v.siren = %s"; values.append(digits)
        else:
            where += " AND (v.legal_name ILIKE %s OR v.trading_name ILIKE %s OR v.establishment_name ILIKE %s)"
            values += [_like(q)] * 3
    if one("dept"):
        where += " AND v.department_code = %s"; values.append(one("dept").upper()[:3])
    if one("city"):
        where += " AND v.city ILIKE %s"; values.append(_like(one("city")))
    if one("rge") in ("1", "true", "yes"):
        where += " AND (v.rge_labels IS NOT NULL OR v.certifications IS NOT NULL)"
    try:
        page = max(1, int(one("page") or 1))
        per_page = min(MAX_PER_PAGE, max(1, int(one("per_page") or 25)))
    except ValueError:
        page, per_page = 1, 25
    return where, values, page, per_page


def _connect(dsn: str, timeout_ms: int = STATEMENT_TIMEOUT_MS):
    conn = psycopg.connect(dsn, row_factory=dict_row, options=f"-c statement_timeout={timeout_ms}")
    conn.read_only = True
    return conn


def search_companies(dsn: str, params: dict) -> dict:
    where, values, page, per_page = build_query(params)
    with _connect(dsn) as conn:
        total = conn.execute(f"SELECT count(*) AS n {FROM}{where}", values).fetchone()["n"]
        rows = conn.execute(
            f"SELECT {COLUMNS} {FROM}{where} ORDER BY v.legal_name, v.siret LIMIT %s OFFSET %s",
            values + [per_page, (page - 1) * per_page]).fetchall()
    return {"total": total, "page": page, "per_page": per_page, "results": rows}


_stats_cache: dict = {"at": 0.0, "value": None}


STATS_TIMEOUT_MS = 120_000   # the header counts run over the heavy export view: far slower than a page of 25 rows
_stats_lock = threading.Lock()


def _compute_stats(dsn: str) -> dict:
    with _connect(dsn, STATS_TIMEOUT_MS) as conn:
        row = conn.execute(
            f"SELECT count(*) AS establishments, count(DISTINCT v.siren) AS companies, "
            f"count(*) FILTER (WHERE v.rge_labels IS NOT NULL OR v.certifications IS NOT NULL) AS rge_certified, "
            f"count(*) FILTER (WHERE v.website IS NOT NULL) AS with_website {FROM}").fetchone()
    _stats_cache.update(at=time.monotonic(), value=row)
    return row


def _refresh_stats(dsn: str) -> None:
    if not _stats_lock.acquire(blocking=False):
        return  # a refresh is already running
    try:
        _compute_stats(dsn)
    except Exception:  # keep serving the last good value
        pass
    finally:
        _stats_lock.release()


def stats(dsn: str, ttl: float = 900.0) -> dict:
    """Header counts. Cached 15 min; an expired value is served at once while a background thread refreshes it."""
    value = _stats_cache["value"]
    if value is not None:
        if time.monotonic() - _stats_cache["at"] >= ttl:
            threading.Thread(target=_refresh_stats, args=(dsn,), daemon=True).start()
        return value
    return _compute_stats(dsn)


def warm_stats(dsn: str) -> None:
    """Call at startup so the first visitor does not wait for the heavy count."""
    threading.Thread(target=_refresh_stats, args=(dsn,), daemon=True).start()


DETAIL_SECTIONS = {
    "financials": ("SELECT f.fiscal_year, f.period_end, f.duration_months, f.revenue_eur, f.net_result_eur, f.equity_eur, f.cash_eur, "
                   "f.debt_eur, f.employees FROM company_financials f WHERE f.company_id = %s "
                   "ORDER BY f.fiscal_year DESC, f.period_end DESC NULLS LAST LIMIT 5"),
    "certificates": ("SELECT cert_type, cert_label, valid_from, valid_to, is_current, certificate_url, has_attestation "
                     "FROM v_company_certificates WHERE company_id = %s ORDER BY is_current DESC, cert_type, valid_to DESC NULLS LAST LIMIT 50"),
    "activities": ("SELECT facet, label, source_url FROM company_activities WHERE company_id = %s ORDER BY facet, label LIMIT 100"),
    "declarations": ("SELECT kind, value, number, qualifier, source_url FROM company_site_declarations WHERE company_id = %s "
                     "ORDER BY kind, value LIMIT 50"),
    "relations": ("SELECT related_siren, related_name, related_in_base, role, is_active FROM v_company_relations "
                  "WHERE company_id = %s ORDER BY is_active DESC, related_name LIMIT 20"),
    "events": ("SELECT event_type, event_date, detail, basis FROM company_register_events WHERE company_id = %s "
               "ORDER BY event_date DESC LIMIT 20"),
}


def company_detail(dsn: str, siren: str) -> Optional[dict]:
    """Detail sections of one company, or None when it is unknown, a sole proprietor or not diffusible."""
    siren = "".join(ch for ch in (siren or "") if ch.isdigit())
    if len(siren) != 9:
        return None
    with _connect(dsn) as conn:
        head = conn.execute(
            "SELECT company_id, siren, legal_name FROM companies WHERE siren = %s AND is_diffusible AND NOT is_sole_proprietor", (siren,)).fetchone()
        if not head:
            return None
        out = {"siren": head["siren"], "legal_name": head["legal_name"]}
        for name, sql in DETAIL_SECTIONS.items():
            out[name] = conn.execute(sql, (head["company_id"],)).fetchall()
    return out


class DbApi:
    """What fbdb.web calls; tests replace it with a fake."""

    def __init__(self, dsn: Optional[str]):
        self.dsn = dsn

    def handle(self, path: str, params: dict) -> tuple[int, dict]:
        if not self.dsn:
            return 503, {"error": "DATABASE_URL is not configured"}
        if path == "/api/stats":
            return 200, stats(self.dsn)
        if path == "/api/companies":
            return 200, search_companies(self.dsn, params)
        if path == "/api/company":
            siren = params.get("siren")
            detail = company_detail(self.dsn, (siren[0] if isinstance(siren, list) else siren) or "")
            return (200, detail) if detail else (404, {"error": "not found"})
        return 404, {"error": "not found"}
