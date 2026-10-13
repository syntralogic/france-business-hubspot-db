"""SIRENE API 3.11 delta extraction (weekly incremental).

Endpoint: https://api.insee.fr/api-sirene/3.11/siret   (auth header X-INSEE-Api-Key-Integration,
key obtained by subscribing to "API Sirene" on https://portail-api.insee.fr).

Delta rule (documented by INSEE): query establishments or legal units whose
dateDernierTraitement* is later than the max value already held in our database. The query may return
units whose distributed fields did not change; the diff step filters those out.
Cursor pagination: first call curseur=*, then header.curseurSuivant until it stops changing.
Verify base URL, quotas and field names against the current INSEE documentation before go-live.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterator, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

BASE_URL = os.environ.get("SIRENE_API_BASE", "https://api.insee.fr/api-sirene/3.11")
PAGE_SIZE = 1000
# INSEE public quota: 30 requests per minute (verified 13 Oct 2026, insee.fr + api.gouv.fr). Stay just under it
# instead of living on 429 retries: one call every 2.1 s = 28.5/min.
MIN_INTERVAL_S = float(os.environ.get("SIRENE_MIN_INTERVAL_S", "2.1"))
log = logging.getLogger("fbdb.sirene_api")


class RetryableError(Exception):
    pass


class Throttle:
    """Minimum spacing between calls (thread-safe). Injectable clock/sleep so it is testable."""

    def __init__(self, min_interval: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval, self._clock, self._sleep = min_interval, clock, sleep
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None:
                delay = self.min_interval - (now - self._last)
                if delay > 0:
                    self._sleep(delay)
                    now = self._clock()
            self._last = now


def _to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def flatten_etablissement(item: dict) -> dict:
    """Flatten an API establishment (with embedded legal unit, address, current period) into the
    same column names as the bulk CSV so the bulk mappers can be reused unchanged."""
    flat: dict = {}
    for k, v in item.items():
        if not isinstance(v, (dict, list)):
            flat[k] = _to_str(v)
    flat.update({k: _to_str(v) for k, v in (item.get("adresseEtablissement") or {}).items() if not isinstance(v, (dict, list))})
    periods = item.get("periodesEtablissement") or []
    current = next((p for p in periods if p.get("dateFin") is None), periods[0] if periods else {})
    flat.update({k: _to_str(v) for k, v in current.items() if not isinstance(v, (dict, list))})
    flat.update({k: _to_str(v) for k, v in (item.get("uniteLegale") or {}).items() if not isinstance(v, (dict, list))})
    return flat


class SireneApiClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0):
        key = api_key or os.environ["INSEE_API_KEY"]
        self._throttle = Throttle(MIN_INTERVAL_S)
        self._client = httpx.Client(
            base_url=BASE_URL, headers={"X-INSEE-Api-Key-Integration": key, "Accept": "application/json"}, timeout=timeout
        )

    @retry(
        retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=2, max=120),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        self._throttle.wait()
        resp = self._client.get(path, params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableError(f"HTTP {resp.status_code}")
        if resp.status_code == 404:  # INSEE returns 404 when a query has no result
            return {"header": {}, "etablissements": []}
        resp.raise_for_status()
        return resp.json()

    def changed_since(self, since_iso: str) -> Iterator[dict]:
        """Yield flattened establishment rows changed since `since_iso` (e.g. 2026-10-06T00:00:00)."""
        q = f"dateDernierTraitementEtablissement:[{since_iso} TO *] OR dateDernierTraitementUniteLegale:[{since_iso} TO *]"
        cursor = "*"
        total = 0
        while True:
            data = self._get("/siret", {"q": q, "nombre": PAGE_SIZE, "curseur": cursor, "tri": "siret"})
            items = data.get("etablissements", [])
            for it in items:
                total += 1
                yield flatten_etablissement(it)
            nxt = (data.get("header") or {}).get("curseurSuivant")
            if not items or not nxt or nxt == cursor:
                break
            cursor = nxt
        log.info("SIRENE delta since %s: %d establishments", since_iso, total)
