"""Batch geocoding of establishment addresses with the national address geocoder (BAN data, run by IGN).

Endpoint: POST {GEOCODE_BASE}/search/csv (multipart `data`, repeated `columns`), free and key-less.
The old api-adresse.data.gouv.fr was decommissioned end of January 2026; the Geoplateforme service
(https://data.geopf.fr/geocodage) replaced it: 50 req/s per IP, CSV up to 50 MB. Override with GEOCODE_BASE.

Rule: result_score >= threshold (default 0.6) -> coordinates + standardized address saved, address_status='verified'.
Below threshold or not found -> nothing is overwritten, a `LOW_GEOCODE_SCORE` row is added to data_quality_issues.
A match that only found the TOWN (result_type "municipality": the coordinates are the town centre, not the establishment) is not
"verified" either: same flag (reason MUNICIPALITY_ONLY). The precision (result_type) is stored in geocode_precision; when the
service does not return it the old rule applies. result_type is from the BAN CSV documentation, NOT verified live.
When SIRENE later changes the address, a trigger (sql/007) clears the old coordinates and the row is geocoded again.
Usage: python -m fbdb.enrich.geocode [--limit 50000] [--threshold 0.6] [--chunk 5000]
Needs: sql/007_geocoding.sql, DATABASE_URL.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
from typing import Iterable, Optional

import httpx
import psycopg
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fbdb.extract.sirene_api import RetryableError

log = logging.getLogger("fbdb.geocode")
BASE_URL = os.environ.get("GEOCODE_BASE", "https://data.geopf.fr/geocodage")
THRESHOLD = 0.6
RESULT_COLS = ["result_label", "result_score", "result_status"]


def build_csv(rows: Iterable[dict]) -> str:
    """rows: dicts with id, street, postal_code, city. Returns CSV text (UTF-8)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "street", "postcode", "city"])
    for r in rows:
        w.writerow([r["id"], r.get("street") or "", r.get("postal_code") or "", r.get("city") or ""])
    return buf.getvalue()


def parse_result(text: str, threshold: float = THRESHOLD) -> list[dict]:
    """Parse the geocoder CSV. Each output: id, ok (bool), lat, lon, score, label, reason."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            score = float(r.get("result_score") or 0)
        except ValueError:
            score = 0.0
        found = (r.get("result_status") or "").lower() == "ok"
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            lat = lon = None
            found = False
        precision = (r.get("result_type") or "").strip().lower() or None
        ok = found and score >= threshold and precision != "municipality"
        reason = "" if ok else ("NOT_FOUND" if not found else ("LOW_SCORE" if score < threshold else "MUNICIPALITY_ONLY"))
        out.append({"id": int(r["id"]), "ok": ok, "lat": lat if found else None, "lon": lon if found else None,
                    "score": score, "label": (r.get("result_label") or "").strip(), "reason": reason,
                    "precision": precision if found else None})
    return out


@retry(retry=retry_if_exception_type(RetryableError), stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=2, max=60), reraise=True)
def geocode_csv(csv_text: str, client: Optional[httpx.Client] = None) -> str:
    own = client is None
    client = client or httpx.Client(timeout=180)
    try:
        try:
            resp = client.post(f"{BASE_URL}/search/csv",
                               files={"data": ("addresses.csv", csv_text.encode("utf-8"), "text/csv")},
                               data=[("columns", "street"), ("columns", "postcode"), ("columns", "city"),
                                     ("result_columns", "result_label"), ("result_columns", "result_score"),
                                     ("result_columns", "result_status"), ("result_columns", "latitude"),
                                     ("result_columns", "longitude"), ("result_columns", "result_type")])
        except httpx.TransportError as e:
            raise RetryableError(str(e)) from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableError(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp.text
    finally:
        if own:
            client.close()


SELECT_TODO = """
SELECT establishment_id, company_id, street, postal_code, city FROM company_addresses
WHERE geocoded_at IS NULL AND status = 'active' AND is_diffusible AND postal_code IS NOT NULL
ORDER BY establishment_id LIMIT %s
"""


def run(dsn: str, limit: int = 50000, threshold: float = THRESHOLD, chunk: int = 5000, post=geocode_csv) -> dict:
    stats = {"selected": 0, "verified": 0, "flagged": 0, "errors": 0}
    with psycopg.connect(dsn) as conn:
        todo = conn.execute(SELECT_TODO, (limit,)).fetchall()
        stats["selected"] = len(todo)
        for i in range(0, len(todo), chunk):
            part = todo[i:i + chunk]
            meta = {r[0]: r[1] for r in part}
            try:
                text = post(build_csv({"id": r[0], "street": r[2], "postal_code": r[3], "city": r[4]} for r in part))
            except Exception as e:  # noqa: BLE001 - one bad chunk must not stop the run; rows stay un-geocoded
                log.error("chunk %s failed: %s", i // chunk, e)
                stats["errors"] += len(part)
                continue
            for res in parse_result(text, threshold):
                eid = res["id"]
                if eid not in meta:
                    continue
                if res["ok"]:
                    conn.execute(
                        """UPDATE company_addresses SET latitude=%s, longitude=%s, standardized_address=%s,
                           address_status='verified', geocode_score=%s, geocode_precision=%s, geocoded_at=now()
                           WHERE establishment_id=%s""",
                        (res["lat"], res["lon"], res["label"] or None, res["score"], res["precision"], eid))
                    stats["verified"] += 1
                else:
                    conn.execute("UPDATE company_addresses SET geocode_score=%s, geocode_precision=%s, geocoded_at=now() "
                                 "WHERE establishment_id=%s", (res["score"], res["precision"], eid))
                    conn.execute(
                        """INSERT INTO data_quality_issues(company_id, establishment_id, issue_code, severity, field_name, details)
                           VALUES (%s,%s,'LOW_GEOCODE_SCORE','warning','address',%s)""",
                        (meta[eid], eid, f"{res['reason']} score={res['score']:.2f} label={res['label']}"))
                    stats["flagged"] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50000)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--chunk", type=int, default=5000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run(os.environ["DATABASE_URL"], a.limit, a.threshold, a.chunk))


if __name__ == "__main__":
    main()
