"""Free demo data that actually matches the client's market: companies that hold an RGE certificate.

  1. ADEME open data (data.ademe.fr, dataset liste-des-entreprises-rge-2, Licence Ouverte, no key): certificate rows.
  2. INSEE Sirene API (INSEE_API_KEY, already needed by the weekly job): the establishment + legal unit for those SIRETs.
  3. Normal loader (companies, establishments), then the normal ADEME RGE loader attaches the certificates.

Optional at web start when the database holds no RGE certificate yet: OFF by default, AUTO_RGE_DEMO=1 enables it
(RGE_DEMO_COMPANIES to resize, default 3000). Manual: python -m fbdb.load.rge_demo [--companies 3000]
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
from fbdb.enrich.annuaire import AnnuaireClient
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


def _s(v) -> str:
    return "" if v is None else str(v)


def flat_from_annuaire(result: dict, siret: str) -> Optional[dict]:
    """Annuaire des Entreprises result + one of its establishments -> the stock-file column names the loader maps."""
    if _s(result.get("siren")) != siret[:9]:
        return None
    etab = next((e for e in [*(result.get("matching_etablissements") or []), result.get("siege") or {}]
                 if _s(e.get("siret")) == siret), None)
    if etab is None:
        return None
    return {
        "siren": result["siren"], "siret": siret,
        "denominationUniteLegale": _s(result.get("nom_raison_sociale") or result.get("nom_complet")),
        "categorieJuridiqueUniteLegale": _s(result.get("nature_juridique")),
        "activitePrincipaleUniteLegale": _s(result.get("activite_principale")),
        "trancheEffectifsUniteLegale": _s(result.get("tranche_effectif_salarie")),
        "categorieEntreprise": _s(result.get("categorie_entreprise")),
        "etatAdministratifUniteLegale": _s(result.get("etat_administratif")),
        "dateCreationUniteLegale": _s(result.get("date_creation")),
        "statutDiffusionUniteLegale": _s(result.get("statut_diffusion") or "O"),
        "etablissementSiege": "true" if etab.get("est_siege") else "false",
        "enseigne1Etablissement": _s(etab.get("nom_commercial")),
        "activitePrincipaleEtablissement": _s(etab.get("activite_principale")),
        "etatAdministratifEtablissement": _s(etab.get("etat_administratif")),
        "dateCreationEtablissement": _s(etab.get("date_creation")),
        "numeroVoieEtablissement": _s(etab.get("numero_voie")),
        "indiceRepetitionEtablissement": _s(etab.get("indice_repetition")),
        "typeVoieEtablissement": _s(etab.get("type_voie")),
        "libelleVoieEtablissement": _s(etab.get("libelle_voie")),
        "complementAdresseEtablissement": _s(etab.get("complement_adresse")),
        "codePostalEtablissement": _s(etab.get("code_postal")),
        "libelleCommuneEtablissement": _s(etab.get("libelle_commune")),
    }


def fetch_annuaire(sirets: list[str], client: Optional[AnnuaireClient] = None) -> Iterator[dict]:
    """Key-less fallback: one free lookup per SIRET (about 4 per second), so the demo size is capped."""
    client = client or AnnuaireClient()
    for siret in sirets[:int(os.environ.get("RGE_FALLBACK_MAX", "1500"))]:
        try:
            data = client._get({"q": siret, "per_page": 1})
        except httpx.HTTPError as exc:
            log.warning("annuaire lookup failed for %s: %s", siret, exc)
            continue
        for res in data.get("results") or []:
            flat = flat_from_annuaire(res, siret)
            if flat:
                yield flat


def fetch_establishments(sirets: list[str], api: Optional[SireneApiClient] = None) -> Iterator[dict]:
    """Sirene API when the key works, otherwise the free Annuaire des Entreprises."""
    try:
        api = api or SireneApiClient()
        first = api._get("/siret", {"q": siret_query(sirets[:BATCH]), "nombre": min(BATCH, len(sirets))})
    except (KeyError, httpx.HTTPStatusError) as exc:
        log.warning("Sirene API not usable (%s): using the free Annuaire des Entreprises, smaller sample", type(exc).__name__)
        yield from fetch_annuaire(sirets)
        return
    for item in first.get("etablissements", []):
        yield flatten_etablissement(item)
    for i in range(BATCH, len(sirets), BATCH):
        chunk = sirets[i:i + BATCH]
        for item in api._get("/siret", {"q": siret_query(chunk), "nombre": len(chunk)}).get("etablissements", []):
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
