import httpx

from fbdb.load import rge_demo as r


def test_clean_siret():
    assert r.clean_siret("123 456 789 00012") == "12345678900012"
    assert r.clean_siret("123") is None and r.clean_siret(None) is None


def test_siret_query():
    assert r.siret_query(["1", "2"]) == "siret:1 OR siret:2"


def test_ademe_rows_follows_next_and_stops_at_target():
    pages = {
        "https://x.test/lines": {"results": [{"siret": "11111111111111", "q": "a"}, {"siret": "11111111111111", "q": "b"}],
                                 "next": "https://x.test/lines?after=1"},
        "https://x.test/lines?after=1": {"results": [{"siret": "22222222222222"}, {"siret": "33333333333333"}], "next": None},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[str(req.url).split("&size")[0].split("?size")[0] if "after" not in str(req.url) else str(req.url)])

    old = r.ADEME_LINES
    r.ADEME_LINES = "https://x.test/lines"
    try:
        rows, sirets = r.ademe_rows(2, client=httpx.Client(transport=httpx.MockTransport(handler)))
    finally:
        r.ADEME_LINES = old
    assert sirets == ["11111111111111", "22222222222222"]
    assert len(rows) == 3   # both certificates of the first company, one of the second; the third SIRET is over target


def test_flat_from_annuaire_maps_stock_columns_and_loader_accepts_it():
    from fbdb.extract.sirene_bulk import map_etablissement, map_unite_legale
    res = {"siren": "123456789", "nom_raison_sociale": "DUPONT ISOLATION", "nature_juridique": "5499",
           "activite_principale": "43.29A", "etat_administratif": "A", "date_creation": "2015-03-02",
           "siege": {"siret": "12345678900012", "est_siege": True, "activite_principale": "43.29A", "etat_administratif": "A",
                     "numero_voie": "12", "type_voie": "RUE", "libelle_voie": "DES LILAS", "code_postal": "69003",
                     "libelle_commune": "LYON 3EME"}}
    flat = r.flat_from_annuaire(res, "12345678900012")
    ul, et = map_unite_legale(flat), map_etablissement(flat)
    assert ul["legal_name"] == "DUPONT ISOLATION" and ul["status"] == "active" and not ul["is_sole_proprietor"]
    assert et["street"] == "12 RUE DES LILAS" and et["postal_code"] == "69003" and et["department_code"] == "69"
    assert et["is_head_office"] is True


def test_flat_from_annuaire_rejects_other_siren():
    assert r.flat_from_annuaire({"siren": "999999999", "siege": {"siret": "12345678900012"}}, "12345678900012") is None
