"""HubSpot Companies export: column spec, row building, validation, CSV + error report writers.

One HubSpot company = one active establishment (SIRET). SIREN groups establishments of the same legal entity.
CSV headers are HubSpot property LABELS (HubSpot auto-maps by label). Custom properties must be created by the
client with exactly these labels before import (see docs/07-hubspot-mapping.md).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator, Optional

from fbdb.transform.normalize import is_valid_siren, is_valid_siret, normalize_domain, normalize_email, normalize_phone_fr

MAX_ROWS_PER_FILE = 1_000_000  # HubSpot hard limit is 1,048,576 rows per file

# (CSV header label, HubSpot internal name, standard?)
COLUMNS: list[tuple[str, str, bool]] = [
    ("Company name", "name", True),
    ("Company domain name", "domain", True),
    ("Phone Number", "phone", True),
    ("Street Address", "address", True),
    ("Street Address 2", "address2", True),
    ("City", "city", True),
    ("State/Region", "state", True),
    ("Postal Code", "zip", True),
    ("Country/Region", "country", True),
    ("Website URL", "website", True),
    ("LinkedIn Company Page", "linkedin_company_page", True),
    ("Number of Employees", "numberofemployees", True),
    ("Annual Revenue", "annualrevenue", True),
    ("SIRET", "siret", False),
    ("SIREN", "siren", False),
    ("Raison sociale", "raison_sociale", False),
    ("Code NAF", "code_naf", False),
    ("Libelle NAF", "libelle_naf", False),
    ("Metier", "metier", False),
    ("Forme juridique", "forme_juridique", False),
    ("Siege", "est_siege", False),
    ("Statut etablissement", "statut_etablissement", False),
    ("Date creation", "date_creation", False),
    ("Date cloture", "date_cloture", False),
    ("Departement code", "departement_code", False),
    ("Departement", "departement_nom", False),
    ("Region", "region", False),
    ("Categorie taille", "categorie_taille", False),
    ("Tranche effectifs", "tranche_effectifs", False),
    ("Annee CA", "annee_ca", False),
    ("Certifications", "certifications", False),
    ("Dirigeant prenom", "dirigeant_prenom", False),
    ("Dirigeant nom", "dirigeant_nom", False),
    ("Dirigeant fonction", "dirigeant_fonction", False),
    ("Email professionnel", "email_pro", False),
    ("Statut email", "statut_email", False),
    ("Statut telephone", "statut_telephone", False),
    ("Source contact", "source_contact", False),
    ("Source principale", "source_principale", False),
    ("Date verification", "date_verification", False),
    ("Score confiance", "score_confiance", False),
    ("Fraicheur donnees", "fraicheur_donnees", False),
    ("Note Google", "note_google", False),
    ("Nombre avis Google", "nb_avis_google", False),
    ("URL Google", "url_google", False),
    ("ID interne", "id_interne", False),
]
HEADERS = [c[0] for c in COLUMNS]

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CP = re.compile(r"^[0-9]{2}[0-9AB][0-9]{2}$")
ERROR_HEADERS = ["row_number", "siret", "id_interne", "severity", "code", "field", "message", "value"]


@dataclass
class Issue:
    row: int
    siret: str
    internal_id: str
    severity: str  # 'error' = row rejected, 'warning' = field blanked, row kept
    code: str
    field: str
    message: str
    value: str = ""

    def as_row(self) -> list:
        return [self.row, self.siret, self.internal_id, self.severity, self.code, self.field, self.message, self.value]


def _s(v) -> str:
    if v is None:
        return ""
    return _CTRL.sub("", str(v)).strip()


def build_row(src: dict) -> dict:
    """Map an internal export-view row (see sql/004_export_view.sql) to HubSpot columns (by internal name)."""
    name = _s(src.get("establishment_name")) or _s(src.get("trading_name")) or _s(src.get("legal_name"))
    return {
        "name": name,
        "domain": normalize_domain(src.get("website")) or "",
        "phone": _s(src.get("phone")),
        "address": _s(src.get("street")),
        "address2": _s(src.get("complement")),
        "city": _s(src.get("city")),
        "state": _s(src.get("region")),
        "zip": _s(src.get("postal_code")),
        "country": _s(src.get("country")) or "France",
        "website": _s(src.get("website")),
        "linkedin_company_page": _s(src.get("linkedin")),
        "numberofemployees": _s(src.get("employees")),
        "annualrevenue": _s(src.get("revenue")),
        "siret": _s(src.get("siret")),
        "siren": _s(src.get("siren")),
        "raison_sociale": _s(src.get("legal_name")),
        "code_naf": _s(src.get("naf")),
        "libelle_naf": _s(src.get("naf_label")),
        "metier": _s(src.get("trade")),
        "forme_juridique": _s(src.get("legal_form")),
        "est_siege": "true" if src.get("is_head_office") else "false",
        "statut_etablissement": _s(src.get("status")),
        "date_creation": _s(src.get("creation_date")),
        "date_cloture": _s(src.get("closure_date")),
        "departement_code": _s(src.get("department_code")),
        "departement_nom": _s(src.get("department_name")),
        "region": _s(src.get("region")),
        "categorie_taille": _s(src.get("size_category")),
        "tranche_effectifs": _s(src.get("employee_range")),
        "annee_ca": _s(src.get("revenue_year")),
        "certifications": _s(src.get("certifications")),
        "dirigeant_prenom": _s(src.get("director_first")),
        "dirigeant_nom": _s(src.get("director_last")),
        "dirigeant_fonction": _s(src.get("director_role")),
        "email_pro": _s(src.get("email")),
        "statut_email": _s(src.get("email_status")),
        "statut_telephone": _s(src.get("phone_status")),
        "source_contact": _s(src.get("contact_source")),
        "source_principale": _s(src.get("primary_source")),
        "date_verification": _s(src.get("last_verified_at"))[:10],
        "score_confiance": _s(src.get("confidence")),
        "fraicheur_donnees": _s(src.get("freshness")),
        "note_google": _s(src.get("google_rating")),
        "nb_avis_google": _s(src.get("google_reviews")),
        "url_google": _s(src.get("google_url")),
        "id_interne": _s(src.get("internal_id")),
    }


def validate_row(row: dict, row_no: int, src: dict, include_closed: bool = False) -> list[Issue]:
    """Mutates `row` to blank invalid optional fields (warnings) and returns issues; any 'error' rejects the row."""
    siret = row["siret"]
    iid = row["id_interne"]
    out: list[Issue] = []

    def err(code, field, msg, val=""):
        out.append(Issue(row_no, siret, iid, "error", code, field, msg, _s(val)))

    def warn(code, field, msg, val=""):
        out.append(Issue(row_no, siret, iid, "warning", code, field, msg, _s(val)))

    if src.get("is_diffusible") is False:
        err("NON_DIFFUSIBLE", "is_diffusible", "Non-diffusible unit must never be exported")
    if not row["name"]:
        err("MISSING_NAME", "name", "Company name is required by HubSpot")
    if not is_valid_siret(siret):
        err("INVALID_SIRET", "siret", "SIRET must be 14 digits and pass Luhn", siret)
    if not is_valid_siren(row["siren"]):
        err("INVALID_SIREN", "siren", "SIREN must be 9 digits and pass Luhn", row["siren"])
    elif siret and not siret.startswith(row["siren"]):
        err("SIREN_SIRET_MISMATCH", "siren", "SIRET does not start with SIREN", row["siren"])
    if row["statut_etablissement"] != "active" and not include_closed:
        err("NOT_ACTIVE", "statut_etablissement", "Closed/other status excluded from export", row["statut_etablissement"])
    if row["zip"] and not _CP.match(row["zip"]):
        err("INVALID_POSTAL_CODE", "zip", "Postal code must be 5 characters", row["zip"])
    if not row["city"] and not row["zip"]:
        err("NO_LOCATION", "city", "Neither city nor postal code available")

    # field-level cleanups (row kept)
    if row["phone"]:
        e164 = normalize_phone_fr(row["phone"])
        if e164 is None:
            warn("INVALID_PHONE", "phone", "Phone not valid; blanked", row["phone"])
            row["phone"] = ""
        else:
            row["phone"] = e164
    if row["email_pro"]:
        em = normalize_email(row["email_pro"])
        if em is None:
            warn("INVALID_EMAIL", "email_pro", "Email syntax invalid; blanked", row["email_pro"])
            row["email_pro"] = ""
        elif row["statut_email"] == "inferred":
            warn("INFERRED_EMAIL", "email_pro", "Inferred emails are never exported; blanked", row["email_pro"])
            row["email_pro"] = ""
        else:
            row["email_pro"] = em
    if not row["email_pro"]:
        row["statut_email"] = "unavailable" if not row["statut_email"] or row["statut_email"] == "inferred" else row["statut_email"]
    for f in ("date_creation", "date_cloture", "date_verification"):
        if row[f] and not _ISO_DATE.match(row[f]):
            warn("BAD_DATE", f, "Expected YYYY-MM-DD; blanked", row[f])
            row[f] = ""
    if row["score_confiance"]:
        try:
            if not 0 <= float(row["score_confiance"]) <= 1:
                raise ValueError
        except ValueError:
            warn("BAD_CONFIDENCE", "score_confiance", "Must be a number between 0 and 1; blanked", row["score_confiance"])
            row["score_confiance"] = ""
    for f in ("numberofemployees", "annualrevenue", "nb_avis_google", "annee_ca"):
        if row[f] and not re.match(r"^\d+(\.\d+)?$", row[f]):
            warn("BAD_NUMBER", f, "Must be numeric; blanked", row[f])
            row[f] = ""
    return out


def export_companies(
    records: Iterable[dict], out_dir: Path, run_date: Optional[date] = None, include_closed: bool = False
) -> dict:
    """Write hubspot_companies_<date>_partNN.csv files (UTF-8, no BOM) and an error report CSV."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()
    seen_siret: set[str] = set()
    seen_domain: set[str] = set()
    n_ok = n_err = n_warn = 0
    part = 0
    in_part = 0
    fh = writer = None
    err_path = out_dir / f"hubspot_errors_{run_date.isoformat()}.csv"
    files: list[Path] = []

    with open(err_path, "w", newline="", encoding="utf-8") as ef:
        ew = csv.writer(ef)
        ew.writerow(ERROR_HEADERS)
        try:
            for i, src in enumerate(records, start=1):
                row = build_row(src)
                issues = validate_row(row, i, src, include_closed)
                if row["siret"] in seen_siret:
                    issues.append(Issue(i, row["siret"], row["id_interne"], "error", "DUPLICATE_SIRET", "siret", "SIRET already present in this file", row["siret"]))
                if row["domain"]:
                    if row["domain"] in seen_domain:
                        # HubSpot merges companies sharing a domain: keep domain on the first row only
                        issues.append(Issue(i, row["siret"], row["id_interne"], "warning", "DOMAIN_SHARED", "domain", "Domain already used by another row; domain blanked to avoid merge", row["domain"]))
                        row["domain"] = ""
                    else:
                        seen_domain.add(row["domain"])
                for iss in issues:
                    ew.writerow(iss.as_row())
                n_warn += sum(1 for x in issues if x.severity == "warning")
                if any(x.severity == "error" for x in issues):
                    n_err += 1
                    continue
                seen_siret.add(row["siret"])
                if writer is None or in_part >= MAX_ROWS_PER_FILE:
                    if fh:
                        fh.close()
                    part += 1
                    p = out_dir / f"hubspot_companies_{run_date.isoformat()}_part{part:02d}.csv"
                    files.append(p)
                    fh = open(p, "w", newline="", encoding="utf-8")
                    writer = csv.writer(fh)
                    writer.writerow(HEADERS)
                    in_part = 0
                writer.writerow([row[c[1]] for c in COLUMNS])
                in_part += 1
                n_ok += 1
        finally:
            if fh:
                fh.close()
    return {"exported": n_ok, "rejected": n_err, "warnings": n_warn, "files": [str(f) for f in files], "error_report": str(err_path)}


def iter_csv_records(path: Path) -> Iterator[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)
