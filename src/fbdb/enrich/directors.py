"""Weekly directors refresh from the Annuaire des Entreprises API.

For a bounded number of companies (never checked first, then oldest check first) it:
  * fetches the current directors (natural persons),
  * compares them with the active rows of `company_directors`,
  * inserts new directors, flags directors that disappeared as inactive (end_date = today),
  * writes every addition/removal to `company_change_history` (old and new value),
  * stamps `companies.directors_checked_at` so empty results are not re-queried every week,
  * records the run in `import_export_batches` (batch_type = 'enrich').

Scope guards (see docs/09-security-privacy.md): only active, diffusible companies that are NOT sole proprietors.

Usage: python -m fbdb.enrich.directors [--limit 5000] [--refresh-days 30]
Needs: sql/005_directors_enrichment.sql applied, DATABASE_URL set.
"""
from __future__ import annotations

import argparse
import logging
import os

import psycopg

from fbdb.enrich.annuaire import AnnuaireClient, map_dirigeants, map_legal_entity_officers
from fbdb.enrich.company_relations import refresh_relations

log = logging.getLogger("fbdb.directors")
MAX_CONSECUTIVE_ERRORS = 20
COMMIT_EVERY = 100

SELECT_DUE = """
SELECT company_id, siren FROM companies
WHERE status = 'active' AND is_diffusible AND NOT is_sole_proprietor
  AND (directors_checked_at IS NULL OR directors_checked_at < now() - make_interval(days => %(days)s))
  AND (%(shards)s = 1 OR company_id %% %(shards)s = %(shard)s)   -- parallel workers take disjoint slices
ORDER BY directors_checked_at NULLS FIRST, company_id
LIMIT %(limit)s
"""


def _label(last: str, first: str, role: str) -> str:
    name = f"{last} {first}".strip()
    return f"{name} ({role})" if role else name


def refresh_company(cur, company_id: int, mapped: list[dict], source_id: int, batch_id: int) -> tuple[int, int]:
    """Apply one company's directors. Returns (directors added, directors removed)."""
    cur.execute(
        "SELECT director_id, last_name, first_name, coalesce(role, '') FROM company_directors "
        "WHERE company_id = %s AND is_active",
        (company_id,),
    )
    active = {(r[1] or "", r[2] or "", r[3]): r[0] for r in cur.fetchall()}
    wanted = {(m["last_name"], m["first_name"], m["role"]): m for m in mapped}

    added = removed = 0
    for key, m in wanted.items():
        if key in active:
            cur.execute("UPDATE company_directors SET last_verified_at = now() WHERE director_id = %s", (active[key],))
            continue
        cur.execute(
            "INSERT INTO company_directors (company_id, first_name, last_name, role, birth_year, is_active, source_id, "
            "last_verified_at) VALUES (%s, %s, %s, %s, %s, TRUE, %s, now()) "
            "ON CONFLICT (company_id, last_name, first_name, role) DO UPDATE SET is_active = TRUE, end_date = NULL, "
            "birth_year = EXCLUDED.birth_year, source_id = EXCLUDED.source_id, last_verified_at = now()",
            (company_id, m["first_name"], m["last_name"], m["role"], m["birth_year"], source_id),
        )
        cur.execute(
            "INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, "
            "source_id, batch_id) VALUES (%s, 'company_directors', 'director', NULL, %s, 'insert', %s, %s)",
            (company_id, _label(*key), source_id, batch_id),
        )
        added += 1
    for key, director_id in active.items():
        if key in wanted:
            continue
        cur.execute(
            "UPDATE company_directors SET is_active = FALSE, end_date = current_date, last_verified_at = now() "
            "WHERE director_id = %s",
            (director_id,),
        )
        cur.execute(
            "INSERT INTO company_change_history (company_id, table_name, field_name, old_value, new_value, change_type, "
            "source_id, batch_id) VALUES (%s, 'company_directors', 'director', %s, NULL, 'delete', %s, %s)",
            (company_id, _label(*key), source_id, batch_id),
        )
        removed += 1
    cur.execute("UPDATE companies SET directors_checked_at = now() WHERE company_id = %s", (company_id,))
    return added, removed


def run(dsn: str, client=None, limit: int = 5000, refresh_days: int = 30, shards: int = 1, shard: int = 0) -> dict:
    client = client or AnnuaireClient()
    stats = {"read": 0, "added": 0, "removed": 0, "updated": 0, "unchanged": 0, "not_found": 0, "errors": 0, "relations_changed": 0}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_id FROM data_sources WHERE code = 'annuaire'")
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO import_export_batches (batch_type, source_id) VALUES ('enrich', %s) RETURNING batch_id",
            (source_id,),
        )
        batch_id = cur.fetchone()[0]
        conn.commit()
        cur.execute(SELECT_DUE, {"days": refresh_days, "limit": limit, "shards": shards, "shard": shard})
        due = cur.fetchall()
        consecutive_errors = 0
        status, note = "success", None
        try:
            for i, (company_id, siren) in enumerate(due, 1):
                stats["read"] += 1
                try:
                    result = client.get_company(siren)
                    consecutive_errors = 0
                except Exception as exc:  # network/HTTP failure after retries: skip this company, keep the batch alive
                    stats["errors"] += 1
                    consecutive_errors += 1
                    log.warning("directors lookup failed for %s: %s", siren, exc)
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(f"{consecutive_errors} consecutive API failures, aborting") from exc
                    continue
                if result is None:
                    stats["not_found"] += 1
                    cur.execute("UPDATE companies SET directors_checked_at = now() WHERE company_id = %s", (company_id,))
                    continue
                added, removed = refresh_company(cur, company_id, map_dirigeants(result), source_id, batch_id)
                stats["added"] += added
                stats["removed"] += removed
                stats["relations_changed"] += refresh_relations(cur, company_id, map_legal_entity_officers(result), source_id, batch_id)
                if added or removed:
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
                if i % COMMIT_EVERY == 0:
                    conn.commit()
            if stats["errors"]:
                status, note = "partial", f"{stats['errors']} lookups failed and will be retried next run"
        except Exception as exc:
            status, note = "failed", str(exc)[:1000]
            raise
        finally:
            conn.commit()  # keep what was already enriched: every company is processed independently
            cur.execute(
                "UPDATE import_export_batches SET status = %s, finished_at = now(), rows_read = %s, rows_added = %s, "
                "rows_updated = %s, rows_unchanged = %s, rows_review = %s, notes = %s WHERE batch_id = %s",
                (status, stats["read"], stats["added"], stats["updated"], stats["unchanged"], stats["errors"], note, batch_id),
            )
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000, help="max companies checked per run")
    ap.add_argument("--refresh-days", type=int, default=30, help="re-check companies not checked for N days")
    ap.add_argument("--shard", default="0/1", help="i/n: this worker takes companies with company_id %% n == i (run n workers in parallel)")
    args = ap.parse_args()
    shard, shards = (int(x) for x in args.shard.split("/"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(os.environ["DATABASE_URL"], limit=args.limit, refresh_days=args.refresh_days, shards=shards, shard=shard))


if __name__ == "__main__":
    main()
