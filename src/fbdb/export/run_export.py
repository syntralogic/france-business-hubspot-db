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
    ap.add_argument("--siret-is-import-key", action="store_true",
                    help="keep the shared domain on every establishment (only if the import uses the unique custom property SIRET as identifier)")
    ap.add_argument("--rating-max-age-days", type=int, default=30, help="with --allow-google-ratings: blank a Google rating fetched longer ago")
    ap.add_argument("--allow-google-ratings", action="store_true", help="export Google rating/review count (only with a written Google agreement or licensed provider)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor(name="export_cur", row_factory=dict_row) as cur:  # server-side cursor: constant memory
            cur.itersize = 20_000
            cur.execute("SELECT * FROM v_hubspot_companies ORDER BY siren, is_head_office DESC, siret")
            result = export_companies(cur, args.out, include_closed=args.include_closed, siret_is_import_key=args.siret_is_import_key, allow_google_ratings=args.allow_google_ratings, max_rating_age_days=args.rating_max_age_days)
    log.info("Export finished: %s", result)
    print(result)


if __name__ == "__main__":
    main()
