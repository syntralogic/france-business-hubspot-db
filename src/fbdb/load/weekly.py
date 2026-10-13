"""Weekly incremental update.

Steps:
 1. read watermark (max dateDernierTraitement already processed)
 2. fetch delta from SIRENE API and COPY into staging
 3. write change history (old vs new) and count added/updated/closed/unchanged
 4. upsert into master tables (reuses the bulk upserts)
 5. flag stale/closed records, advance watermark, write the weekly report

Usage: python -m fbdb.load.weekly [--since 2026-10-06T00:00:00]
Idempotent: re-running the same week produces zero additional history rows (diff finds no differences).
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import date
from pathlib import Path

import psycopg

from fbdb.extract.sirene_api import SireneApiClient
from fbdb.extract.sirene_bulk import map_etablissement, map_unite_legale
from fbdb.load.sirene_loader import (
    ETAB_STG_COLS, UL_STG_COLS, UPDATE_ARTISAN_FLAG, UPSERT_COMPANIES, UPSERT_ESTABLISHMENTS, _copy_rows,
)

log = logging.getLogger("fbdb.weekly")
WATERMARK_KEY = "sirene_last_processed"
REPORT_DIR = Path("reports")

# --- diff SQL: executed BEFORE the upserts so old values are still in the master tables -------------

HISTORY_COMPANY_CHANGES = """
INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, source_id, batch_id)
SELECT c.company_id, 'companies', f.field, f.old_v, f.new_v,
       CASE WHEN f.field = 'status' AND f.new_v = 'closed' THEN 'close'
            WHEN f.field = 'status' AND f.new_v = 'active' THEN 'reopen' ELSE 'update' END,
       (SELECT source_id FROM data_sources WHERE code='sirene'), %(batch)s
FROM stg_sirene_ul s
JOIN companies c ON c.siren = s.siren
CROSS JOIN LATERAL (VALUES
  ('legal_name',      c.legal_name,                s.legal_name),
  ('trading_name',    c.trading_name,              s.trading_name),
  ('legal_form_code', c.legal_form_code,           s.legal_form_code),
  ('main_naf',        c.main_naf,                  s.main_naf),
  ('employee_range',  c.employee_range,            s.employee_range),
  ('status',          c.status,                    s.status),
  ('is_diffusible',   c.is_diffusible::text,       s.is_diffusible::text)
) AS f(field, old_v, new_v)
WHERE s.legal_name IS NOT NULL AND f.old_v IS DISTINCT FROM f.new_v;
"""

HISTORY_ESTAB_CHANGES = """
INSERT INTO company_change_history (company_id, establishment_id, table_name, field_name, old_value, new_value, change_type, source_id, batch_id)
SELECT a.company_id, a.establishment_id, 'company_addresses', f.field, f.old_v, f.new_v,
       CASE WHEN f.field = 'status' AND f.new_v = 'closed' THEN 'close'
            WHEN f.field = 'status' AND f.new_v = 'active' THEN 'reopen' ELSE 'update' END,
       (SELECT source_id FROM data_sources WHERE code='sirene'), %(batch)s
FROM stg_sirene_etab e
JOIN company_addresses a ON a.siret = e.siret
CROSS JOIN LATERAL (VALUES
  ('street',             a.street,             e.street),
  ('postal_code',        a.postal_code::text,  e.postal_code::text),
  ('city',               a.city,               e.city),
  ('naf',                a.naf,                e.naf),
  ('establishment_name', a.establishment_name, e.establishment_name),
  ('is_head_office',     a.is_head_office::text, e.is_head_office::text),
  ('status',             a.status,             e.status)
) AS f(field, old_v, new_v)
WHERE e.is_diffusible AND f.old_v IS DISTINCT FROM f.new_v;
"""

# After the upsert, newly inserted rows exist: log them as inserts (rows created in this batch)
HISTORY_AFTER_INSERT_COMPANIES = """
INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, source_id, batch_id)
SELECT c.company_id, 'companies', 'siren', NULL, c.siren, 'insert', c.primary_source_id, %(batch)s
FROM companies c JOIN stg_sirene_ul s ON s.siren = c.siren
WHERE c.created_at >= %(started)s;
"""
HISTORY_AFTER_INSERT_ESTABS = """
INSERT INTO company_change_history (company_id, establishment_id, table_name, field_name, old_value, new_value, change_type, source_id, batch_id)
SELECT a.company_id, a.establishment_id, 'company_addresses', 'siret', NULL, a.siret, 'insert', a.source_id, %(batch)s
FROM company_addresses a JOIN stg_sirene_etab e ON e.siret = a.siret
WHERE a.updated_at >= %(started)s AND NOT EXISTS (
  SELECT 1 FROM company_change_history h WHERE h.establishment_id = a.establishment_id AND h.change_type = 'insert');
"""

# Closures seen in this batch that SIRENE gave no exact date for: record the date INSEE processed the change
# (dateDernierTraitement) and mark it 'observed', so an approximate date is never mistaken for a legal one.
OBSERVED_CLOSURE_ESTABS = """
UPDATE company_addresses a
SET closure_date = CASE WHEN e.source_last_processed ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                        THEN left(e.source_last_processed, 10)::date ELSE current_date END,
    closure_date_basis = 'observed'
FROM stg_sirene_etab e
WHERE e.siret = a.siret AND a.status = 'closed' AND a.closure_date IS NULL
  AND EXISTS (SELECT 1 FROM company_change_history h
              WHERE h.batch_id = %(batch)s AND h.change_type = 'close' AND h.establishment_id = a.establishment_id);
"""
OBSERVED_CLOSURE_COMPANIES = """
UPDATE companies c
SET closure_date = CASE WHEN s.source_last_processed ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                        THEN left(s.source_last_processed, 10)::date ELSE current_date END,
    closure_date_basis = 'observed'
FROM stg_sirene_ul s
WHERE s.siren = c.siren AND c.status = 'closed' AND c.closure_date IS NULL
  AND EXISTS (SELECT 1 FROM company_change_history h
              WHERE h.batch_id = %(batch)s AND h.change_type = 'close' AND h.company_id = c.company_id
                AND h.establishment_id IS NULL);
"""

COUNT_NEW_COMPANIES ="SELECT count(*) FROM stg_sirene_ul s WHERE s.legal_name IS NOT NULL AND NOT EXISTS (SELECT 1 FROM companies c WHERE c.siren = s.siren)"
COUNT_NEW_ESTABS = "SELECT count(*) FROM stg_sirene_etab e WHERE e.is_diffusible AND NOT EXISTS (SELECT 1 FROM company_addresses a WHERE a.siret = e.siret)"
COUNT_TOTAL_ESTABS = "SELECT count(*) FROM stg_sirene_etab"
COUNT_CHANGED_ENTITIES = "SELECT count(DISTINCT coalesce(establishment_id::text, 'c'||company_id::text)) FROM company_change_history WHERE batch_id=%(batch)s AND change_type IN ('update','close','reopen')"
COUNT_CLOSED = "SELECT count(*) FROM company_change_history WHERE batch_id=%(batch)s AND change_type='close'"
COUNT_REVIEW = "SELECT count(*) FROM data_quality_issues WHERE status='open'"

FRESHNESS_UPDATE = """
UPDATE companies SET freshness_status = CASE
  WHEN updated_at >= now() - interval '30 days' THEN 'fresh'
  WHEN updated_at >= now() - interval '180 days' THEN 'stale' ELSE 'expired' END
WHERE freshness_status IS DISTINCT FROM CASE
  WHEN updated_at >= now() - interval '30 days' THEN 'fresh'
  WHEN updated_at >= now() - interval '180 days' THEN 'stale' ELSE 'expired' END;
"""


def build_report(stats: dict, run_date: date) -> str:
    lines = [
        f"# Weekly update report - {run_date.isoformat()}",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Establishments read from delta | {stats['read']} |",
        f"| Added (new companies) | {stats['added_companies']} |",
        f"| Added (new establishments) | {stats['added_establishments']} |",
        f"| Updated (changed records) | {stats['updated']} |",
        f"| Closed | {stats['closed']} |",
        f"| Unchanged | {stats['unchanged']} |",
        f"| Requiring review (open issues) | {stats['review']} |",
        "",
        f"Watermark advanced to: {stats.get('watermark', 'n/a')}",
    ]
    return "\n".join(lines) + "\n"


def run(since: str | None, dsn: str, client=None) -> dict:
    client = client or SireneApiClient()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if since is None:
            cur.execute("SELECT value FROM pipeline_state WHERE key=%s", (WATERMARK_KEY,))
            row = cur.fetchone()
            if not row:
                raise SystemExit("No watermark: run the bulk SIRENE load first, or pass --since.")
            since = row[0]
        cur.execute("SELECT now()")
        started = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO import_export_batches (batch_type, source_id) "
            "SELECT 'import', source_id FROM data_sources WHERE code='sirene' RETURNING batch_id"
        )
        batch = cur.fetchone()[0]
        conn.commit()
        try:
            cur.execute("TRUNCATE stg_sirene_ul, stg_sirene_etab")
            rows = list(client.changed_since(since))  # ~100k-300k rows/week: fits in memory
            ul_seen: dict[str, dict] = {}
            for raw in rows:
                m = map_unite_legale(raw)
                if m:
                    ul_seen[m["siren"]] = m
            etabs = [m for m in (map_etablissement(r) for r in rows) if m]
            _copy_rows(cur, "stg_sirene_ul", UL_STG_COLS, ul_seen.values())
            _copy_rows(cur, "stg_sirene_etab", ETAB_STG_COLS, etabs)

            p = {"batch": batch, "started": started}
            cur.execute(COUNT_NEW_COMPANIES); added_c = cur.fetchone()[0]
            cur.execute(COUNT_NEW_ESTABS); added_e = cur.fetchone()[0]
            cur.execute(COUNT_TOTAL_ESTABS); total = cur.fetchone()[0]
            cur.execute(HISTORY_COMPANY_CHANGES, p)
            cur.execute(HISTORY_ESTAB_CHANGES, p)
            cur.execute(UPSERT_COMPANIES)
            cur.execute(UPSERT_ESTABLISHMENTS)
            cur.execute(UPDATE_ARTISAN_FLAG)
            cur.execute(OBSERVED_CLOSURE_ESTABS, p)
            cur.execute(OBSERVED_CLOSURE_COMPANIES, p)
            cur.execute(HISTORY_AFTER_INSERT_COMPANIES, p)
            cur.execute(HISTORY_AFTER_INSERT_ESTABS, p)
            cur.execute(COUNT_CHANGED_ENTITIES, p); updated = cur.fetchone()[0]
            cur.execute(COUNT_CLOSED, p); closed = cur.fetchone()[0]
            cur.execute(COUNT_REVIEW); review = cur.fetchone()[0]
            cur.execute(FRESHNESS_UPDATE)

            new_wm = max((r.get("source_last_processed") or "" for r in etabs), default="") or since
            cur.execute(
                "INSERT INTO pipeline_state(key,value) VALUES (%s,%s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                (WATERMARK_KEY, new_wm),
            )
            stats = {
                "read": total, "added_companies": added_c, "added_establishments": added_e, "updated": updated,
                "closed": closed, "unchanged": max(total - added_e - updated, 0), "review": review, "watermark": new_wm,
            }
            cur.execute(
                "UPDATE import_export_batches SET status='success', finished_at=now(), rows_read=%s, rows_added=%s, "
                "rows_updated=%s, rows_closed=%s, rows_unchanged=%s, rows_review=%s WHERE batch_id=%s",
                (total, added_e, updated, closed, stats["unchanged"], review, batch),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            cur.execute(
                "UPDATE import_export_batches SET status='failed', finished_at=now(), notes=%s WHERE batch_id=%s",
                (str(exc)[:1000], batch),
            )
            conn.commit()
            raise
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"weekly_{date.today().isoformat()}.md").write_text(build_report(stats, date.today()), encoding="utf-8")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO timestamp, overrides stored watermark")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(args.since, os.environ["DATABASE_URL"]))


if __name__ == "__main__":
    main()
