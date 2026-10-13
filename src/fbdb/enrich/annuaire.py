"""Annuaire des Entreprises (recherche-entreprises.api.gouv.fr) client and director mapping.

Free, key-less public API. Used here ONLY for per-SIREN enrichment (directors), never to bulk-copy the register:
it does not expose the full Sirene base (see docs/13-verification-log.md).

Response shape verified on 13 Oct 2026: `results[i].dirigeants` is a list whose entries are either
  personne physique : nom, prenoms, annee_de_naissance, date_de_naissance, qualite, nationalite, type_dirigeant
  personne morale   : siren, denomination, qualite, type_dirigeant
We keep natural persons only, and only the birth YEAR (data minimisation, see docs/09-security-privacy.md).

The public rate limit is not documented in the response; the client spaces calls conservatively
(ANNUAIRE_MIN_INTERVAL_S, default 0.25 s) and retries 429/5xx with exponential backoff. Confirm the current
limit on api.gouv.fr before raising the throughput.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fbdb.extract.sirene_api import RetryableError, Throttle

BASE_URL = os.environ.get("ANNUAIRE_API_BASE", "https://recherche-entreprises.api.gouv.fr")
MIN_INTERVAL_S = float(os.environ.get("ANNUAIRE_MIN_INTERVAL_S", "0.25"))


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def map_dirigeants(result: dict) -> list[dict]:
    """Map the `dirigeants` of one API result to rows for company_directors (natural persons only).

    Returns dicts with: last_name (UPPER), first_name (first given name, Title Case), role ('' if unknown),
    birth_year (int or None). Duplicates (same last/first/role) are collapsed.
    """
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for d in result.get("dirigeants") or []:
        if _clean(d.get("type_dirigeant")).lower() != "personne physique":
            continue
        last = _clean(d.get("nom")).upper()
        if not last:
            continue
        given = _clean(d.get("prenoms")).split(",")[0].strip()
        first = given.title() if given else ""
        role = _clean(d.get("qualite"))
        key = (last, first, role)
        if key in seen:
            continue
        seen.add(key)
        year = _clean(d.get("annee_de_naissance"))
        out.append({
            "last_name": last,
            "first_name": first,
            "role": role,
            "birth_year": int(year) if re.fullmatch(r"(19|20)\d{2}", year) else None,
        })
    return out


class AnnuaireClient:
    def __init__(self, timeout: float = 30.0):
        self._throttle = Throttle(MIN_INTERVAL_S)
        self._client = httpx.Client(base_url=BASE_URL, headers={"Accept": "application/json"}, timeout=timeout)

    @retry(
        retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=2, max=120),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _get(self, params: dict) -> dict:
        self._throttle.wait()
        resp = self._client.get("/search", params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableError(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    def get_company(self, siren: str) -> Optional[dict]:
        """Return the API result whose SIREN matches exactly, or None if the API does not know it."""
        data = self._get({"q": siren, "per_page": 1})
        for res in data.get("results") or []:
            if res.get("siren") == siren:
                return res
        return None
