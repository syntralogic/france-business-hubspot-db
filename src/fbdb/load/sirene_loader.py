"""Load SIRENE stock files into PostgreSQL: CSV -> staging (COPY) -> master (upsert).

Usage:
    python -m fbdb.load.sirene_loader --ul StockUniteLegale_utf8.csv --etab StockEtablissement_utf8.csv [--only-active]

Requires DATABASE_URL in the environment. Step 7 (weekly pipeline) adds change-history diffing
before the upsert; this module covers the initial/bulk load.
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import psycopg

from fbdb.extract.sirene_bulk import map_etablissement, map_unite_legale, read_csv_rows

log = logging.getLogger("fbdb.sirene_loader")

UL_STG_COLS = [
    "siren", "legal_name", "trading_name", "legal_form_code", "main_naf", "is_sole_proprietor",
    "employee_range", "size_category", "status", "creation_date", "is_diffusible", "source_last_processed",
]
ETAB_STG_COLS = [
    "siret", "siren", "nic", "is_head_office", "establishment_name", "naf", "status", "creation_date",
    "closure_date", "street", "complement", "postal_code", "city", "department_code", "is_artisan", "is_diffusible",
    "source_last_processed",
]

UPSERT_COMPANIES = """
INSERT INTO companies (siren, legal_name, trading_name, legal_form_code, main_naf, is_sole_proprietor,
                       employee_range, size_category, status, creation_date, is_diffusible, primary_source_id)
SELECT s.siren, s.legal_name, s.trading_name, s.legal_form_code, s.main_naf, COALESCE(s.is_sole_proprietor, FALSE),
       s.employee_range, s.size_category, COALESCE(s.status, 'active'), s.creation_date,
       COALESCE(s.is_diffusible, TRUE), (SELECT source_id FROM data_sources WHERE code = 'sirene')
FROM stg_sirene_ul s
WHERE s.legal_name IS NOT NULL
ON CONFLICT (siren) DO UPDATE SET
  legal_name = EXCLUDED.legal_name, trading_name = EXCLUDED.trading_name,
  legal_form_code = EXCLUDED.legal_form_code, main_naf = EXCLUDED.main_naf,
  is_sole_proprietor = EXCLUDED.is_sole_proprietor, employee_range = EXCLUDED.employee_range,
  size_category = EXCLUDED.size_category, status = EXCLUDED.status,
  creation_date = EXCLUDED.creation_date, is_diffusible = EXCLUDED.is_diffusible,
  -- a closure date is kept while the company stays closed and cleared if it is reopened
  closure_date = CASE WHEN EXCLUDED.status = 'closed' THEN companies.closure_date END,
  closure_date_basis = CASE WHEN EXCLUDED.status = 'closed' THEN companies.closure_date_basis END,
  updated_at = now()
WHERE (companies.legal_name, companies.trading_name, companies.legal_form_code, companies.main_naf,
       companies.is_sole_proprietor, companies.employee_range, companies.size_category,
       companies.status, companies.creation_date, companies.is_diffusible, companies.closure_date)
   IS DISTINCT FROM
      (EXCLUDED.legal_name, EXCLUDED.trading_name, EXCLUDED.legal_form_code, EXCLUDED.main_naf,
       EXCLUDED.is_sole_proprietor, EXCLUDED.employee_range, EXCLUDED.size_category,
       EXCLUDED.status, EXCLUDED.creation_date, EXCLUDED.is_diffusible,
       CASE WHEN EXCLUDED.status = 'closed' THEN companies.closure_date END);
"""

UPSERT_ESTABLISHMENTS = """
INSERT INTO company_addresses (company_id, siret, nic, is_head_office, establishment_name, naf, status,
                               creation_date, closure_date, closure_date_basis, street, complement, postal_code,
                               city, department_code, source_id)
SELECT c.company_id, e.siret, e.nic, COALESCE(e.is_head_office, FALSE), e.establishment_name, e.naf,
       COALESCE(e.status, 'active'), e.creation_date,
       CASE WHEN e.status = 'closed' THEN e.closure_date END,
       CASE WHEN e.status = 'closed' AND e.closure_date IS NOT NULL THEN 'insee' END,
       e.street, e.complement, e.postal_code, e.city,
       e.department_code, (SELECT source_id FROM data_sources WHERE code = 'sirene')
FROM stg_sirene_etab e
JOIN companies c ON c.siren = e.siren
WHERE e.is_diffusible
ON CONFLICT (siret) DO UPDATE SET
  is_head_office = EXCLUDED.is_head_office, establishment_name = EXCLUDED.establishment_name,
  naf = EXCLUDED.naf, status = EXCLUDED.status, street = EXCLUDED.street,
  complement = EXCLUDED.complement, postal_code = EXCLUDED.postal_code, city = EXCLUDED.city,
  department_code = EXCLUDED.department_code,
  -- exact INSEE date wins; otherwise keep what we already hold while closed; cleared if reopened
  closure_date = CASE WHEN EXCLUDED.status = 'closed'
                      THEN COALESCE(EXCLUDED.closure_date, company_addresses.closure_date) END,
  closure_date_basis = CASE WHEN EXCLUDED.status = 'closed'
                            THEN CASE WHEN EXCLUDED.closure_date IS NOT NULL THEN 'insee'
                                      ELSE company_addresses.closure_date_basis END END,
  updated_at = now()
WHERE (company_addresses.is_head_office, company_addresses.establishment_name, company_addresses.naf,
       company_addresses.status, company_addresses.street, company_addresses.complement,
       company_addresses.postal_code, company_addresses.city, company_addresses.department_code,
       company_addresses.closure_date)
   IS DISTINCT FROM
      (EXCLUDED.is_head_office, EXCLUDED.establishment_name, EXCLUDED.naf, EXCLUDED.status,
       EXCLUDED.street, EXCLUDED.complement, EXCLUDED.postal_code, EXCLUDED.city, EXCLUDED.department_code,
       CASE WHEN EXCLUDED.status = 'closed'
            THEN COALESCE(EXCLUDED.closure_date, company_addresses.closure_date) END);
"""

# The upsert above skips establishments INSEE does not diffuse (its fields are masked). One that WAS diffusible and is not
# any more must stop being exported / sent to providers: flag it (nothing is deleted); diffused again -> flag cleared and the
# upsert refreshed its fields in the same run.
SYNC_ESTABLISHMENT_DIFFUSION = """
UPDATE company_addresses a SET is_diffusible = e.is_diffusible, updated_at = now()
FROM stg_sirene_etab e
WHERE a.siret = e.siret AND e.is_diffusible IS NOT NULL AND a.is_diffusible IS DISTINCT FROM e.is_diffusible;
"""

SET_WATERMARK = """
INSERT INTO pipeline_state (key, value)
SELECT 'sirene_last_processed', max(source_last_processed) FROM stg_sirene_etab
HAVING max(source_last_processed) IS NOT NULL
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
"""

UPDATE_ARTISAN_FLAG = """
UPDATE companies c SET is_artisan = a.flag
FROM (SELECT siren, bool_or(is_artisan) AS flag FROM stg_sirene_etab GROUP BY siren) a
WHERE c.siren = a.siren AND c.is_artisan IS DISTINCT FROM a.flag;
"""


def _copy_rows(cur: psycopg.Cursor, table: str, cols: list[str], rows, flush_every: int = 200_000) -> int:
    n = 0
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r.get(c) for c in cols])
            n += 1
            if n % flush_every == 0:
                log.info("%s: %d rows copied", table, n)
    return n


def load(ul_path: Path, etab_path: Path, only_active: bool, dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO import_export_batches (batch_type, source_id) "
                "SELECT 'import', source_id FROM data_sources WHERE code = 'sirene' RETURNING batch_id"
            )
            batch_id = cur.fetchone()[0]
            conn.commit()
            try:
                cur.execute("TRUNCATE stg_sirene_ul, stg_sirene_etab")
                n_ul = _copy_rows(cur, "stg_sirene_ul", UL_STG_COLS, read_csv_rows(ul_path, map_unite_legale, only_active))
                n_et = _copy_rows(cur, "stg_sirene_etab", ETAB_STG_COLS, read_csv_rows(etab_path, map_etablissement, only_active))
                cur.execute(UPSERT_COMPANIES)
                cur.execute(UPSERT_ESTABLISHMENTS)
                cur.execute(SYNC_ESTABLISHMENT_DIFFUSION)
                cur.execute(UPDATE_ARTISAN_FLAG)
                cur.execute(SET_WATERMARK)
                cur.execute(
                    "UPDATE import_export_batches SET status='success', finished_at=now(), rows_read=%s WHERE batch_id=%s",
                    (n_ul + n_et, batch_id),
                )
                conn.commit()
                log.info("Load finished: %d legal units, %d establishments", n_ul, n_et)
            except Exception as exc:
                conn.rollback()
                cur.execute(
                    "UPDATE import_export_batches SET status='failed', finished_at=now(), notes=%s WHERE batch_id=%s",
                    (str(exc)[:1000], batch_id),
                )
                conn.commit()
                raise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ul", type=Path, required=True)
    ap.add_argument("--etab", type=Path, required=True)
    ap.add_argument("--only-active", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load(args.ul, args.etab, args.only_active, os.environ["DATABASE_URL"])


if __name__ == "__main__":
    main()
