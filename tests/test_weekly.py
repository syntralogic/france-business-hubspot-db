from datetime import date

from fbdb.extract.sirene_api import flatten_etablissement
from fbdb.extract.sirene_bulk import map_etablissement, map_unite_legale
from fbdb.load.weekly import build_report

API_ITEM = {
    "siren": "552100554", "nic": "00013", "siret": "55210055400013",
    "statutDiffusionEtablissement": "O", "dateCreationEtablissement": "2012-03-01",
    "dateDernierTraitementEtablissement": "2026-10-08T14:22:10.000000", "etablissementSiege": True,
    "uniteLegale": {
        "denominationUniteLegale": "BOULANGERIE DUPONT", "categorieJuridiqueUniteLegale": "5499",
        "etatAdministratifUniteLegale": "A", "activitePrincipaleUniteLegale": "10.71C",
        "statutDiffusionUniteLegale": "O", "dateCreationUniteLegale": "2012-03-01",
        "dateDernierTraitementUniteLegale": "2026-10-07T01:00:00.000000",
    },
    "adresseEtablissement": {
        "numeroVoieEtablissement": "12", "typeVoieEtablissement": "RUE", "libelleVoieEtablissement": "DE LA PAIX",
        "codePostalEtablissement": "69003", "libelleCommuneEtablissement": "LYON 3EME",
    },
    "periodesEtablissement": [
        {"dateFin": "2020-01-01", "etatAdministratifEtablissement": "A", "enseigne1Etablissement": "OLD SIGN"},
        {"dateFin": None, "etatAdministratifEtablissement": "F", "enseigne1Etablissement": "AU FOURNIL",
         "activitePrincipaleEtablissement": "10.71C"},
    ],
}


def test_flatten_uses_current_period_and_reuses_bulk_mappers():
    flat = flatten_etablissement(API_ITEM)
    et = map_etablissement(flat)
    assert et["status"] == "closed"                     # current period has etat F
    assert et["establishment_name"] == "AU FOURNIL"     # not the ended period's sign
    assert et["is_head_office"] is True                 # JSON boolean handled
    assert et["street"] == "12 RUE DE LA PAIX" and et["postal_code"] == "69003"
    ul = map_unite_legale(flat)
    assert ul["legal_name"] == "BOULANGERIE DUPONT" and ul["status"] == "active"


def test_report_contains_all_required_counts():
    stats = {"read": 100, "added_companies": 5, "added_establishments": 7, "updated": 20,
             "closed": 3, "unchanged": 73, "review": 2, "watermark": "2026-10-08T14:22:10"}
    r = build_report(stats, date(2026, 10, 13))
    for needle in ("2026-10-13", "Added", "Updated", "Closed", "Unchanged", "review", "2026-10-08T14:22:10"):
        assert needle in r


def test_flatten_exposes_closure_date_and_ignores_embedded_legal_unit_dates():
    item = {**API_ITEM, "uniteLegale": {**API_ITEM["uniteLegale"], "dateDebut": "1999-01-01"},
            "periodesEtablissement": [
                {"dateFin": "2025-06-29", "dateDebut": "2020-01-01", "etatAdministratifEtablissement": "A"},
                {"dateFin": None, "dateDebut": "2025-06-30", "etatAdministratifEtablissement": "F",
                 "changementEtatAdministratifEtablissement": True},
            ]}
    et = map_etablissement(flatten_etablissement(item))
    assert et["status"] == "closed" and et["closure_date"] == "2025-06-30"   # not the legal unit's 1999 date
