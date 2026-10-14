from fbdb.extract.sirene_bulk import (
    ETAB_COLUMNS, UL_COLUMNS, department_from_postal, map_etablissement, map_unite_legale, read_csv_rows,
)


def ul(**kw):
    base = {c: "" for c in UL_COLUMNS}
    base.update(siren="552100554", statutDiffusionUniteLegale="O", denominationUniteLegale="BOULANGERIE DUPONT",
               categorieJuridiqueUniteLegale="5499", etatAdministratifUniteLegale="A",
               dateCreationUniteLegale="2012-03-01", activitePrincipaleUniteLegale="10.71C")
    base.update(kw)
    return base


def et(**kw):
    base = {c: "" for c in ETAB_COLUMNS}
    base.update(siren="552100554", nic="00013", siret="55210055400013", statutDiffusionEtablissement="O",
               etablissementSiege="true", etatAdministratifEtablissement="A", numeroVoieEtablissement="12",
               typeVoieEtablissement="RUE", libelleVoieEtablissement="DE LA PAIX", codePostalEtablissement="69003",
               libelleCommuneEtablissement="LYON 3EME", activitePrincipaleEtablissement="10.71C")
    base.update(kw)
    return base


def test_company_mapping():
    m = map_unite_legale(ul())
    assert m["siren"] == "552100554" and m["status"] == "active" and m["is_diffusible"] is True
    assert m["is_sole_proprietor"] is False and m["main_naf"] == "10.71C"


def test_sole_proprietor_uses_person_name_and_flag():
    m = map_unite_legale(ul(denominationUniteLegale="", categorieJuridiqueUniteLegale="1000",
                            nomUniteLegale="MARTIN", prenomUsuelUniteLegale="PAUL"))
    assert m["is_sole_proprietor"] is True and m["legal_name"] == "PAUL MARTIN"


def test_ceased_and_non_diffusible():
    m = map_unite_legale(ul(etatAdministratifUniteLegale="C", statutDiffusionUniteLegale="N", denominationUniteLegale="[ND]"))
    assert m["status"] == "closed" and m["is_diffusible"] is False and m["legal_name"] is None


def test_invalid_siren_rejected():
    assert map_unite_legale(ul(siren="12AB")) is None


def test_establishment_mapping():
    m = map_etablissement(et())
    assert m["street"] == "12 RUE DE LA PAIX" and m["department_code"] == "69" and m["is_head_office"] is True
    assert m["nic"] == "00013" and m["is_artisan"] is False


def test_artisan_flag_and_siret_mismatch():
    assert map_etablissement(et(activitePrincipaleRegistreMetiersEtablissement="1071CA"))["is_artisan"] is True
    assert map_etablissement(et(siret="99999999900013")) is None  # siret does not start with siren


def test_departments():
    assert department_from_postal("20000") == "2A"
    assert department_from_postal("20200") == "2B"
    assert department_from_postal("97100") == "971"
    assert department_from_postal("75001") == "75"
    assert department_from_postal("") is None


def test_csv_reader_filters_active(tmp_path):
    p = tmp_path / "ul.csv"
    import csv
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=UL_COLUMNS)
        w.writeheader()
        w.writerow(ul())
        w.writerow(ul(siren="732829320", etatAdministratifUniteLegale="C"))
    assert len(list(read_csv_rows(p, map_unite_legale))) == 2
    assert len(list(read_csv_rows(p, map_unite_legale, only_active=True))) == 1


def test_closure_date_only_when_the_period_began_with_the_state_change():
    closed = dict(etatAdministratifEtablissement="F", dateDebutPeriodeEtablissement="2025-06-30")
    # period began with the closure: exact date
    assert map_etablissement(et(changementEtatAdministratifEtablissement="true", **closed))["closure_date"] == "2025-06-30"
    # period began for another reason (e.g. a later sign change): the date would be wrong, so it stays unknown
    assert map_etablissement(et(changementEtatAdministratifEtablissement="false", **closed))["closure_date"] is None
    # bulk stock rows carry neither key: unknown, never guessed
    assert map_etablissement(et(etatAdministratifEtablissement="F"))["closure_date"] is None
    # an active establishment never has a closure date
    assert map_etablissement(et(changementEtatAdministratifEtablissement="true",
                                dateDebutPeriodeEtablissement="2025-06-30"))["closure_date"] is None


def test_old_style_or_malformed_naf_becomes_none_not_a_db_error():
    # Regression found by the synthetic scale test: NAFRev1-style "151A" aborted the whole bulk load (DB CHECK).
    assert map_unite_legale(ul(activitePrincipaleUniteLegale="151A"))["main_naf"] is None
    assert map_unite_legale(ul(activitePrincipaleUniteLegale="10.71C"))["main_naf"] == "10.71C"
    assert map_etablissement(et(activitePrincipaleEtablissement="1071"))["naf"] is None
    assert map_etablissement(et(activitePrincipaleEtablissement="43.22A"))["naf"] == "43.22A"
