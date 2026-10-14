"""Free demo data that actually matches the client's market: companies that hold an RGE certificate.

  1. ADEME open data (data.ademe.fr, dataset liste-des-entreprises-rge-2, Licence Ouverte, no key): certificate rows.
  2. INSEE Sirene API (INSEE_API_KEY, already needed by the weekly job): the establishment + legal unit for those SIRETs.
  3. Normal loader (companies, establishments), then the normal ADEME RGE loader attaches the certificates.

Automatic at web start when the database holds no RGE certificate yet (AUTO_RGE_DEMO=0 to disable,
RGE_DEMO_COMPANIES to resize, default 3000). Manual: python -m fbdb.load.rge_demo [--companies 3000]
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Iterator, Optional

import httpx
import psycopg

from fbdb.enrich import ademe_rge
from fbdb.extract.sirene_api import SireneApiClient, flatten_etablissement
from fbdb.load.sirene_loader import load

log = logging.getLogger("fbdb.rge_demo")
ADEME_LINES = os.environ.get(
    "ADEME_RGE_LINES", "https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2/lines")
BATCH = 40   # SIRETs per Sirene request


def clean_siret(value) -> Optional[str]:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 14 else None


def ademe_rows(target: int, page_size: int = 1000, client: Optional[httpx.Client] = None) -> tuple[list[dict], list[str]]:
    """Certificate rows of the first `target` distinct SIRETs (follows the API's `next` links)."""
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    rows, sirets, url, params = [], [], ADEME_LINES, {"size": page_size}
    seen: set[str] = set()
    try:
        while url and len(seen) < target:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            page = data.get("results") or []
            if not page:
                break
            for row in page:
                s = clean_siret(row.get("siret") or row.get("SIRET"))
                if s and (s in seen or len(seen) < target):
                    if s not in seen:
                        seen.add(s)
                        sirets.append(s)
                    rows.append(row)
            url, params = data.get("next"), None
    finally:
        if own:
            client.close()
    return rows, sirets


def siret_query(sirets: list[str]) -> str:
    return " OR ".join(f"siret:{s}" for s in sirets)


def fetch_establishments(sirets: list[str], api: Optional[SireneApiClient] = None) -> Iterator[dict]:
    api = api or SireneApiClient()
    for i in range(0, len(sirets), BATCH):
        chunk = sirets[i:i + BATCH]
        data = api._get("/siret", {"q": siret_query(chunk), "nombre": len(chunk)})
        for item in data.get("etablissements", []):
            yield flatten_etablissement(item)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for r in rows:
        fields += [k for k in r if k not in fields]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def rge_demo(dsn: str, companies: int = 3000) -> dict:
    rows, sirets = ademe_rows(companies)
    if not sirets:
        raise RuntimeError("ADEME returned no RGE rows (check ADEME_RGE_LINES)")
    log.info("rge demo: %d certificate rows for %d SIRETs", len(rows), len(sirets))
    flat = list(fetch_establishments(sirets))
    log.info("rge demo: %d establishments returned by the Sirene API", len(flat))
    if not flat:
        raise RuntimeError("Sirene API returned nothing (check INSEE_API_KEY)")
    by_siren: dict[str, dict] = {}
    for f in flat:
        by_siren.setdefault(f.get("siren", ""), f)   # one legal-unit row per SIREN (the upsert needs unique keys)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        write_csv(t / "ul.csv", list(by_siren.values()))
        write_csv(t / "etab.csv", flat)
        load(t / "ul.csv", t / "etab.csv", True, dsn)
        write_csv(t / "rge.csv", rows)
        stats = ademe_rge.run(dsn, str(t / "rge.csv"), str(t / "rge_rejects.csv"))
    log.info("rge demo done: %s", stats)
    return stats


def has_rge(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT EXISTS (SELECT 1 FROM company_certifications WHERE cert_type = 'RGE')").fetchone()[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--companies", type=int, default=int(os.environ.get("RGE_DEMO_COMPANIES", "3000")))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    rge_demo(os.environ["DATABASE_URL"], args.companies)


if __name__ == "__main__":
    main()
