"""Generate examples/hubspot_companies_SAMPLE_FICTIONAL.csv from 5 FICTIONAL records.

Every name carries "(EXEMPLE FICTIF)", phones use ARCEP fiction ranges, domains/emails use the reserved
.example TLD. SIREN/SIRET values are generated to pass the Luhn check and are NOT real companies
(a generated number can coincidentally exist in the real register: never import this file).
"""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fbdb.export.hubspot import export_companies  # noqa: E402
from fbdb.transform.normalize import is_valid_siren, is_valid_siret  # noqa: E402


def make_siren(base8: str) -> str:
    return next(base8 + str(d) for d in range(10) if is_valid_siren(base8 + str(d)))


def make_siret(siren: str, nic4: str) -> str:
    return next(siren + nic4 + str(d) for d in range(10) if is_valid_siret(siren + nic4 + str(d)))


S1, S2, S3, S4 = (make_siren(b) for b in ("90000001", "90000002", "90000003", "90000004"))
COMMON = {"country": "France", "is_diffusible": True, "status": "active", "primary_source": "sirene",
          "freshness": "fresh", "last_verified_at": "2026-10-12"}

RECORDS = [
    dict(COMMON, internal_id=1, siret=make_siret(S1, "0001"), siren=S1, legal_name="BOULANGERIE LUMIERE (EXEMPLE FICTIF)",
         establishment_name="Au Fournil Fictif (EXEMPLE FICTIF)", street="12 RUE DE LA PAIX", postal_code="69003", city="LYON 3EME",
         department_code="69", department_name="Rhone", region="Auvergne-Rhone-Alpes", naf="10.71C",
         naf_label="Boulangerie et boulangerie-patisserie", trade="Boulanger", legal_form="SARL", is_head_office=True,
         creation_date="2012-03-01", phone="04 65 71 00 01", phone_status="sourced", contact_source="licensed_contacts",
         website="https://www.fournil-fictif.example", employee_range="3 a 5 salaries", size_category="TPE",
         confidence=0.93, director_first="Marie", director_last="Exemple", director_role="Gerante"),
    dict(COMMON, internal_id=2, siret=make_siret(S1, "0002"), siren=S1, legal_name="BOULANGERIE LUMIERE (EXEMPLE FICTIF)",
         establishment_name="Au Fournil Fictif Villeurbanne (EXEMPLE FICTIF)", street="3 COURS EMILE ZOLA", postal_code="69100",
         city="VILLEURBANNE", department_code="69", department_name="Rhone", region="Auvergne-Rhone-Alpes",
         naf="10.71C", naf_label="Boulangerie et boulangerie-patisserie", trade="Boulanger", legal_form="SARL",
         is_head_office=False, creation_date="2018-09-15", phone="04 65 71 00 02", phone_status="sourced",
         contact_source="licensed_contacts", website="https://www.fournil-fictif.example", size_category="TPE", confidence=0.88),
    dict(COMMON, internal_id=3, siret=make_siret(S2, "0001"), siren=S2, legal_name="PLOMBERIE DU MIDI (EXEMPLE FICTIF)",
         street="48 ALLEES JEAN JAURES", postal_code="31000", city="TOULOUSE", department_code="31",
         department_name="Haute-Garonne", region="Occitanie", naf="43.22A", naf_label="Travaux d'installation d'eau et de gaz",
         trade="Plombier", legal_form="EI", is_head_office=True, creation_date="2019-05-20", phone="05 65 71 00 03",
         phone_status="sourced", contact_source="licensed_contacts", employee_range="0 salarie", size_category="TPE", confidence=0.81),
    dict(COMMON, internal_id=4, siret=make_siret(S3, "0001"), siren=S3, legal_name="CABINET LEROY & ASSOCIES (EXEMPLE FICTIF)",
         street="5 RUE VICTOR HUGO", postal_code="75001", city="PARIS 1ER", department_code="75", department_name="Paris",
         region="Ile-de-France", naf="69.10Z", naf_label="Activites juridiques", trade="Avocat", legal_form="SELARL",
         is_head_office=True, creation_date="2005-11-02", phone="01 99 00 00 04", phone_status="verified",
         contact_source="licensed_contacts", email="Contact@Cabinet-Fictif.example", email_status="sourced",
         website="https://cabinet-fictif.example", employees=12, revenue=1450000, revenue_year=2024,
         size_category="PME", confidence=0.95, director_first="Jean", director_last="Fictif", director_role="Gerant"),
    dict(COMMON, internal_id=5, siret=make_siret(S4, "0001"), siren=S4, legal_name="ELECTRICITE MULLER (EXEMPLE FICTIF)",
         street="21 RUE DES ORFEVRES", postal_code="67000", city="STRASBOURG", department_code="67", department_name="Bas-Rhin",
         region="Grand Est", naf="43.21A", naf_label="Travaux d'installation electrique", trade="Electricien",
         legal_form="SAS", is_head_office=True, creation_date="2010-01-10", phone="03 53 01 00 05", phone_status="sourced",
         contact_source="licensed_contacts", certifications="RGE (exemple)", google_rating=4.6, google_reviews=87,
         google_url="https://maps.google.com/?cid=0000000000000000000", employee_range="6 a 9 salaries",
         size_category="TPE", confidence=0.9, director_first="Anna", director_last="Muller", director_role="President"),
]

if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp())
    res = export_companies(RECORDS, tmp, run_date=date(2026, 10, 13))
    out = Path(__file__).resolve().parents[1] / "examples"
    out.mkdir(exist_ok=True)
    shutil.copy(res["files"][0], out / "hubspot_companies_SAMPLE_FICTIONAL.csv")
    shutil.copy(res["error_report"], out / "hubspot_errors_SAMPLE_FICTIONAL.csv")
    print(res)
