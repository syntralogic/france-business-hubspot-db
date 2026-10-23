"""Empty the company data of a database (to remove a demo slice before the real load).

    DATABASE_URL=<postgres url> python -m fbdb.ops.purge_all            # DRY RUN: prints what would be deleted, deletes nothing
    DATABASE_URL=<postgres url> python -m fbdb.ops.purge_all --yes      # deletes

Deletes every company and everything attached to it (addresses, contacts, people, certifications, financials, activity, press, social
accounts, conflicts... every table that references companies, followed through the foreign keys) and restarts the internal ids.
KEPT: reference tables (NAF, legal forms, data sources), the import / export history, the paid-API usage counters (api_usage, so the daily
cap still counts what was spent today), and any table that does not reference companies.
WARNING: the paid-enrichment memory tables hang on companies too: after a purge a paid module would pay again for a company it had already
done. Purge BEFORE any paid run, or not at all. The demo rows are real SIRENE companies (a small slice), so there is no way to tell
"demo" rows from real ones: this command empties everything. A full / scoped SIRENE load afterwards refills the base (upsert on SIREN).
"""
from __future__ import annotations

import argparse
import os

import psycopg

DEPENDENTS_SQL = """
WITH RECURSIVE dep(oid, depth) AS (
  SELECT 'companies'::regclass::oid, 0
  UNION
  SELECT c.conrelid, dep.depth + 1 FROM pg_constraint c JOIN dep ON c.confrelid = dep.oid WHERE c.contype = 'f' AND c.conrelid <> c.confrelid
)
SELECT DISTINCT oid::regclass::text FROM dep ORDER BY 1
"""


def tables_to_purge(conn) -> list[str]:
    return [r[0] for r in conn.execute(DEPENDENTS_SQL).fetchall()]


def count_rows(conn, tables: list[str]) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def purge(dsn: str, confirm: bool = False) -> dict:
    with psycopg.connect(dsn) as conn:
        tables = tables_to_purge(conn)
        counts = count_rows(conn, tables)
        if confirm:
            conn.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
            conn.commit()
    return {"deleted": confirm, "tables": counts}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="really delete (without it: dry run)")
    a = ap.parse_args()
    res = purge(os.environ["DATABASE_URL"], a.yes)
    for t, n in res["tables"].items():
        print(f"{'deleted' if res['deleted'] else 'would delete'} {n:>10}  {t}")
    if not res["deleted"]:
        print("\nDRY RUN: nothing was deleted. Run again with --yes to delete.")


if __name__ == "__main__":
    main()
