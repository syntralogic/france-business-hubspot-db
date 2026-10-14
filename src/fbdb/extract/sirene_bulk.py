"""SIRENE bulk stock files (StockUniteLegale / StockEtablissement) -> staging rows.

Input: the monthly CSV stock files from data.gouv.fr (INSEE). Pure mapping functions are
separated from I/O so they can be unit-tested without the real 1-3 GB files.

Notes on INSEE fields used here (verify against the current file layout before each major release):
- statutDiffusion*: 'O' = fully diffusible, 'P' = partially diffusible, 'N' = not diffusible.
  We keep only 'O' as exportable; 'P' and 'N' are stored with is_diffusible=false and never exported.
- etatAdministratifUniteLegale: 'A' active, 'C' ceased.
- etatAdministratifEtablissement: 'A' active, 'F' closed.
- Coordinates are NOT in the stock file; they come from BAN geocoding or INSEE's separate geolocation file.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator, Optional

UL_COLUMNS = [
    "siren", "statutDiffusionUniteLegale", "dateCreationUniteLegale", "categorieJuridiqueUniteLegale",
    "denominationUniteLegale", "denominationUsuelle1UniteLegale", "nomUniteLegale", "prenomUsuelUniteLegale",
    "activitePrincipaleUniteLegale", "etatAdministratifUniteLegale", "trancheEffectifsUniteLegale",
    "categorieEntreprise", "dateDernierTraitementUniteLegale",
]
ETAB_COLUMNS = [
    "siren", "nic", "siret", "statutDiffusionEtablissement", "dateCreationEtablissement",
    "trancheEffectifsEtablissement", "activitePrincipaleRegistreMetiersEtablissement",
    "etablissementSiege", "etatAdministratifEtablissement", "activitePrincipaleEtablissement",
    "complementAdresseEtablissement", "numeroVoieEtablissement", "indiceRepetitionEtablissement",
    "typeVoieEtablissement", "libelleVoieEtablissement", "codePostalEtablissement",
    "libelleCommuneEtablissement", "enseigne1Etablissement", "denominationUsuelleEtablissement",
    "dateDernierTraitementEtablissement",
]


def _clean(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v if v and v != "[ND]" else None


_NAF_RE = re.compile(r"^\d{2}\.\d{2}[A-Z]$")


def _naf(v: Optional[str]) -> Optional[str]:
    """NAF rev.2 format only (e.g. 10.71C). Old-nomenclature (NAFRev1/NAP) or malformed codes become None instead of
    violating the DB CHECK constraint and aborting a multi-million-row load."""
    v = _clean(v)
    return v if v and _NAF_RE.match(v) else None


def _date(v: Optional[str]) -> Optional[str]:
    v = _clean(v)
    return v if v and len(v) == 10 else None


def department_from_postal(postal: Optional[str]) -> Optional[str]:
    if not postal or len(postal) != 5:
        return None
    if postal.startswith(("97", "98")):
        return postal[:3]
    if postal.startswith("20"):  # Corsica: 200xx-201xx = 2A, 202xx-206xx = 2B
        return "2A" if postal[:3] in ("200", "201") else "2B"
    return postal[:2]


def map_unite_legale(row: dict) -> Optional[dict]:
    siren = _clean(row.get("siren"))
    if not siren or len(siren) != 9 or not siren.isdigit():
        return None
    diffusion = _clean(row.get("statutDiffusionUniteLegale")) or "O"
    legal_form = _clean(row.get("categorieJuridiqueUniteLegale"))
    is_sole = bool(legal_form and legal_form.startswith("1"))
    if _clean(row.get("denominationUniteLegale")):
        name = _clean(row.get("denominationUniteLegale"))
    else:  # natural person
        parts = [_clean(row.get("prenomUsuelUniteLegale")), _clean(row.get("nomUniteLegale"))]
        name = " ".join(p for p in parts if p) or None
    return {
        "siren": siren,
        "legal_name": name,
        "trading_name": _clean(row.get("denominationUsuelle1UniteLegale")),
        "legal_form_code": legal_form,
        "main_naf": _naf(row.get("activitePrincipaleUniteLegale")),
        "is_sole_proprietor": is_sole,
        "employee_range": _clean(row.get("trancheEffectifsUniteLegale")),
        "size_category": _clean(row.get("categorieEntreprise")),
        "status": "active" if _clean(row.get("etatAdministratifUniteLegale")) == "A" else "closed",
        "creation_date": _date(row.get("dateCreationUniteLegale")),
        "is_diffusible": diffusion == "O",
        "source_last_processed": _clean(row.get("dateDernierTraitementUniteLegale")),
    }


def map_etablissement(row: dict) -> Optional[dict]:
    siret = _clean(row.get("siret"))
    siren = _clean(row.get("siren"))
    if not siret or len(siret) != 14 or not siret.isdigit() or not siren or siret[:9] != siren:
        return None
    diffusion = _clean(row.get("statutDiffusionEtablissement")) or "O"
    street_parts = [
        _clean(row.get("numeroVoieEtablissement")), _clean(row.get("indiceRepetitionEtablissement")),
        _clean(row.get("typeVoieEtablissement")), _clean(row.get("libelleVoieEtablissement")),
    ]
    postal = _clean(row.get("codePostalEtablissement"))
    status = "active" if _clean(row.get("etatAdministratifEtablissement")) == "A" else "closed"
    # Exact closure date: only when the current period itself began with the change of administrative state.
    # These two keys come from the API period (see sirene_api.flatten_etablissement); the stock CSV does not
    # carry them, so for bulk loads the date stays NULL and the weekly run records the date it first saw the
    # closure (closure_date_basis = 'observed'). Never guessed.
    closure_date = None
    if status == "closed" and _clean(row.get("changementEtatAdministratifEtablissement")) == "true":
        closure_date = _date(row.get("dateDebutPeriodeEtablissement"))
    return {
        "siret": siret,
        "siren": siren,
        "nic": siret[9:],
        "is_head_office": _clean(row.get("etablissementSiege")) == "true",
        "establishment_name": _clean(row.get("enseigne1Etablissement")) or _clean(row.get("denominationUsuelleEtablissement")),
        "naf": _naf(row.get("activitePrincipaleEtablissement")),
        "status": status,
        "creation_date": _date(row.get("dateCreationEtablissement")),
        "closure_date": closure_date,
        "street": " ".join(p for p in street_parts if p) or None,
        "complement": _clean(row.get("complementAdresseEtablissement")),
        "postal_code": postal if postal and len(postal) == 5 else None,
        "city": _clean(row.get("libelleCommuneEtablissement")),
        "department_code": department_from_postal(postal),
        "is_artisan": bool(_clean(row.get("activitePrincipaleRegistreMetiersEtablissement"))),
        "is_diffusible": diffusion == "O",
        "source_last_processed": _clean(row.get("dateDernierTraitementEtablissement")),
    }


def read_csv_rows(path: Path, mapper, only_active: bool = False) -> Iterator[dict]:
    """Stream a SIRENE CSV (UTF-8, comma separated) and yield mapped rows. Memory-safe for multi-GB files."""
    with open(path, newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            mapped = mapper(raw)
            if mapped is None:
                continue
            if only_active and mapped.get("status") != "active":
                continue
            yield mapped
