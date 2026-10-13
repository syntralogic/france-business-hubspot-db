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
