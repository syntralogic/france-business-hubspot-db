"""Stream v_hubspot_companies from PostgreSQL into HubSpot CSV files + error report.

Usage: python -m fbdb.export.run_export [--out data/exports] [--include-closed]
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from fbdb.export.hubspot import export_companies

log = logging.getLogger("fbdb.export")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/exports"))
    ap.add_argument("--include-closed", action="store_true", help="include closed establishments (for HubSpot status updates)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor(name="export_cur", row_factory=dict_row) as cur:  # server-side cursor: constant memory
            cur.itersize = 20_000
            cur.execute("SELECT * FROM v_hubspot_companies ORDER BY siren, is_head_office DESC, siret")
            result = export_companies(cur, args.out, include_closed=args.include_closed)
    log.info("Export finished: %s", result)
    print(result)


if __name__ == "__main__":
    main()
