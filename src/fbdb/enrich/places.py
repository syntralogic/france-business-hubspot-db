"""Google Places ratings (rating, review count, Maps URL) per establishment. PAID: needs a Google Cloud key with billing.

API: Places API (New) Text Search, POST https://places.googleapis.com/v1/places:searchText with headers
X-Goog-Api-Key and X-Goog-FieldMask (places.id, displayName, formattedAddress, location, rating, userRatingCount, googleMapsUri).
Asking for rating fields bills the higher "Enterprise" Text Search SKU: check the current price and free monthly allowance in
your Google Cloud console. The cost-per-call used for the estimate (PLACES_COST_PER_CALL_USD, default 0.035) is an ASSUMPTION.
Set a budget alert and a quota cap in Google Cloud too: the in-app cap below is a second line of defence, not the only one.
NOT verified against a live key from the build sandbox (blocked): run `--limit 20` first and read the cost note in the output.

SELECTION RULE (who is queried): active establishments of diffusible companies, with a verified geocoded address (so the match
can be checked by distance) and a usable name; optionally restricted by PLACES_NAF_PREFIXES (e.g. "43,56,96") and
PLACES_DEPARTMENTS (e.g. "75,69"). Never queried first, then oldest result; refreshed every 30 days. Daily hard cap on calls:
PLACES_DAILY_CAP (default 100), atomic via fbdb.common.usage.

MATCHING: up to 3 candidates; confidence = 0.6 * name similarity + 0.4 * distance score (1.0 within 100 m, 0 at 500 m or more).
The best candidate is stored with its confidence and place id; the export only uses rows with confidence >= 0.8. No candidate
-> a row with no place id and confidence 0, so the establishment is not asked again before the refresh date.
STATUS (company_reviews.status, sql/021): matched / to_verify (candidate below 0.8, kept, never exported) / not_found (search ran,
no candidate: NOT "no Google page") / inaccessible (the call failed: retried after PLACES_RETRY_DAYS, default 7; a rating already
stored is kept untouched). No row = not searched. A failed call never erases a stored result.

TERMS: Google gives no caching exception for rating / review count (only place_id). The HubSpot export therefore leaves
ratings out unless the client's licence allows it (--allow-google-ratings, see export/hubspot.py).

Usage: python -m fbdb.enrich.places [--limit 100] [--refresh-days 30]
Needs: DATABASE_URL, GOOGLE_PLACES_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
from typing import Optional

import httpx
import psycopg
from rapidfuzz import fuzz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fbdb.common import usage
from fbdb.transform.normalize import normalize_company_name

log = logging.getLogger("fbdb.places")
URL = os.environ.get("PLACES_BASE", "https://places.googleapis.com/v1/places:searchText")
FIELDS = "places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.googleMapsUri"
DAILY_CAP = int(os.environ.get("PLACES_DAILY_CAP", "100"))
COST_PER_CALL = float(os.environ.get("PLACES_COST_PER_CALL_USD", "0.035"))
PROVIDER = "google_places"
MATCH_THRESHOLD = 0.8      # same value as the HubSpot view
RETRY_DAYS = int(os.environ.get("PLACES_RETRY_DAYS", "7"))
MAX_CONSECUTIVE_ERRORS = 10


class RetryableError(Exception):
    pass


class QuotaExhausted(Exception):
    """403/401: key invalid, API not enabled, or billing/quota problem. Stop, do not retry."""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def confidence(our_name: str, lat: float, lon: float, place: dict) -> float:
    theirs = ((place.get("displayName") or {}).get("text")) or ""
    name = fuzz.token_set_ratio(normalize_company_name(our_name), normalize_company_name(theirs)) / 100.0
    loc = place.get("location") or {}
    try:
        d = haversine_m(lat, lon, float(loc["latitude"]), float(loc["longitude"]))
        dist = 1.0 if d <= 100 else max(0.0, 1.0 - (d - 100) / 400)
    except (KeyError, TypeError, ValueError):
        dist = 0.0
    return round(0.6 * name + 0.4 * dist, 3)


def best_match(our_name: str, lat: float, lon: float, places: list[dict]) -> Optional[tuple[dict, float]]:
    scored = [(p, confidence(our_name, lat, lon, p)) for p in places[:3]]
    return max(scored, key=lambda x: x[1]) if scored else None


class PlacesClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0):
        self._key = api_key or os.environ["GOOGLE_PLACES_API_KEY"]
        self._http = httpx.Client(timeout=timeout)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(4), reraise=True)
    def search(self, query: str, lat: float, lon: float) -> list[dict]:
        body = {"textQuery": query, "maxResultCount": 3, "languageCode": "fr", "regionCode": "FR",
                "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 300.0}}}
        r = self._http.post(URL, json=body, headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": FIELDS})
        if r.status_code in (401, 403):
            raise QuotaExhausted(f"Google Places HTTP {r.status_code}: key, API enablement, billing or quota problem")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json().get("places") or []


SELECT_DUE = """
SELECT a.establishment_id, COALESCE(NULLIF(a.establishment_name,''), NULLIF(c.trading_name,''), c.legal_name) AS name,
       a.street, a.postal_code, a.city, a.latitude, a.longitude
FROM company_addresses a
JOIN companies c ON c.company_id = a.company_id
LEFT JOIN company_reviews r ON r.establishment_id = a.establishment_id AND r.provider = 'google_places'
WHERE a.status='active' AND c.is_diffusible AND a.address_status='verified'
  AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL
  AND COALESCE(NULLIF(a.establishment_name,''), NULLIF(c.trading_name,''), c.legal_name) IS NOT NULL
  AND (r.review_id IS NULL
       OR (r.status = 'inaccessible' AND COALESCE(r.last_attempt_at, r.collected_at) < now() - make_interval(days => %(retry)s))
       OR (r.status IS DISTINCT FROM 'inaccessible' AND r.collected_at < now() - make_interval(days => %(days)s)
           AND COALESCE(r.last_attempt_at, r.collected_at) < now() - make_interval(days => %(retry)s)))
  AND (%(naf)s::text[] IS NULL OR EXISTS (SELECT 1 FROM unnest(%(naf)s::text[]) p WHERE COALESCE(a.naf, c.main_naf) LIKE p || '%%'))
  AND (%(dep)s::text[] IS NULL OR a.department_code = ANY(%(dep)s::text[]))
ORDER BY r.collected_at NULLS FIRST, a.establishment_id
LIMIT %(limit)s
"""


def _list_env(name: str) -> Optional[list[str]]:
    v = [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]
    return v or None


def mark_inaccessible(conn, est_id: int, detail: str) -> None:
    """The call failed. Nothing already stored is touched; an establishment with no data yet gets an 'inaccessible' row."""
    conn.execute(
        """INSERT INTO company_reviews (establishment_id, provider, status, last_attempt_at, error_detail)
           VALUES (%s,'google_places','inaccessible',now(),%s)
           ON CONFLICT (establishment_id, provider) DO UPDATE SET last_attempt_at=now(), error_detail=EXCLUDED.error_detail,
             status = CASE WHEN company_reviews.provider_place_id IS NULL AND company_reviews.rating IS NULL
                           THEN 'inaccessible' ELSE company_reviews.status END""",
        (est_id, detail[:200]))


def run(dsn: str, client=None, limit: int = 100, refresh_days: int = 30, cap: int = DAILY_CAP,
        retry_days: int = RETRY_DAYS, naf_prefixes: Optional[list[str]] = None, departments: Optional[list[str]] = None) -> dict:
    client = client or PlacesClient()
    naf_prefixes = naf_prefixes if naf_prefixes is not None else _list_env("PLACES_NAF_PREFIXES")
    departments = departments if departments is not None else _list_env("PLACES_DEPARTMENTS")
    stats = {"read": 0, "matched": 0, "low_confidence_or_none": 0, "errors": 0, "calls": 0,
             "est_cost_usd": 0.0, "cap_reached": False, "stopped_billing": False}
    with psycopg.connect(dsn) as conn:
        due = conn.execute(SELECT_DUE, {"days": refresh_days, "retry": retry_days, "limit": limit, "naf": naf_prefixes, "dep": departments}).fetchall()
        consecutive = 0
        for est_id, name, street, postal, city, lat, lon in due:
            if not usage.reserve(conn, PROVIDER, cap, 1):
                stats["cap_reached"] = True
                log.warning("Places daily cap of %d calls reached; stopping", cap)
                break
            stats["read"] += 1
            lat, lon = float(lat), float(lon)
            try:
                places = client.search(" ".join(x for x in (name, street, postal, city) if x), lat, lon)
                consecutive = 0
            except QuotaExhausted as exc:
                usage.release(conn, PROVIDER, 1)
                stats["stopped_billing"] = True
                log.error("%s", exc)
                break
            except Exception as exc:
                usage.release(conn, PROVIDER, 1)
                stats["errors"] += 1
                consecutive += 1
                log.warning("Places lookup failed for establishment %s: %s", est_id, exc)
                mark_inaccessible(conn, est_id, str(exc))
                conn.commit()
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    break
                continue
            stats["calls"] += 1
            stats["est_cost_usd"] = round(stats["calls"] * COST_PER_CALL, 4)
            m = best_match(name, lat, lon, places)
            place, conf = m if m else ({}, 0.0)
            status = "matched" if conf >= MATCH_THRESHOLD else ("to_verify" if place.get("id") else "not_found")
            conn.execute(
                """INSERT INTO company_reviews (establishment_id, provider, provider_place_id, rating, review_count, profile_url,
                                                match_confidence, collected_at, status, last_attempt_at, error_detail)
                   VALUES (%s,'google_places',%s,%s,%s,%s,%s,now(),%s,now(),NULL)
                   ON CONFLICT (establishment_id, provider) DO UPDATE SET provider_place_id=EXCLUDED.provider_place_id,
                     rating=EXCLUDED.rating, review_count=EXCLUDED.review_count, profile_url=EXCLUDED.profile_url,
                     match_confidence=EXCLUDED.match_confidence, collected_at=now(), status=EXCLUDED.status,
                     last_attempt_at=now(), error_detail=NULL""",
                (est_id, place.get("id"), place.get("rating"), place.get("userRatingCount"), place.get("googleMapsUri"), conf,
                 status))
            conn.commit()
            stats["matched" if conf >= MATCH_THRESHOLD else "low_confidence_or_none"] += 1
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--refresh-days", type=int, default=30)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days)
    print(s)
    print(f"Cost note: {s['calls']} billed calls, estimated ${s['est_cost_usd']} at an ASSUMED ${COST_PER_CALL}/call; check the real bill in Google Cloud.")
    if s["stopped_billing"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
