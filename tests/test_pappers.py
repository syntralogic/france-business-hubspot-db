from fbdb.enrich import pappers
from fbdb.enrich.pappers import parse_finances


def test_parse_finances_filters_sorts_and_limits():
    p = {"finances": [
        {"annee": 2021, "chiffre_affaires": 100000, "resultat": 5000, "effectif": 3},
        {"annee": 2023, "chiffre_affaires": "250000.5", "resultat": None, "effectif": None},
        {"annee": 2022, "chiffre_affaires": None, "effectif": None},      # nothing published: dropped
        {"annee": "bad", "chiffre_affaires": 1},                             # unusable year: dropped
        {"annee": 2020, "effectif": 2}, {"annee": 2019, "chiffre_affaires": 1},
    ]}
    r = parse_finances(p)
    assert [x["year"] for x in r] == [2023, 2021, 2020]
    assert r[0]["revenue"] == 250000.5 and r[0]["employees"] is None and r[1]["net_result"] == 5000
    assert parse_finances({}) == []


def test_parse_finances_keeps_exercise_dates_and_durations():
    from datetime import date
    p = {"finances": [
        {"annee": 2023, "date_cloture_exercice": "2023-06-30", "duree_exercice": 18, "chiffre_affaires": 900},
        {"annee": 2023, "date_cloture_exercice": "31/12/2023", "duree_exercice": "6", "chiffre_affaires": 400},
        {"annee": 2023, "date_cloture_exercice": "31/12/2023", "chiffre_affaires": 1},    # same exercise twice: first kept
        {"annee": 2022, "resultat": -50},                                                  # result alone is kept
        {"annee": 2021, "date_cloture_exercice": "not a date", "duree_exercice": 99, "effectif": 2},   # unreadable: None, not guessed
    ]}
    r = parse_finances(p, keep=10)
    assert [(x["year"], x["period_end"], x["duration_months"]) for x in r] == [
        (2023, date(2023, 12, 31), 6), (2023, date(2023, 6, 30), 18), (2022, None, None), (2021, None, None)]
    assert r[0]["revenue"] == 400 and r[2]["net_result"] == -50


def test_parse_finances_balance_sheet_aliases_and_nothing_invented():
    p = {"finances": [
        {"annee": 2023, "chiffre_affaires": 100, "capitaux_propres": 40000, "tresorerie": "1500.5", "endettement": 0},
        {"annee": 2022, "fonds_propres": -2000, "disponibilites": 30, "dettes_financieres": 900},   # alias names
        {"annee": 2021, "tresorerie": 12},                                                        # a balance figure alone is kept
        {"annee": 2020, "capitaux_propres": "n/a"},                                               # unreadable: dropped, not 0
        {"annee": 2019, "chiffre_affaires": 5},
    ]}
    r = parse_finances(p, keep=10)
    by = {x["year"]: x for x in r}
    assert (by[2023]["equity"], by[2023]["cash"], by[2023]["debt"]) == (40000, 1500.5, 0)   # a published 0 stays 0
    assert (by[2022]["equity"], by[2022]["cash"], by[2022]["debt"]) == (-2000, 30, 900)
    assert by[2021]["cash"] == 12 and by[2021]["equity"] is None and by[2021]["debt"] is None   # missing = None, never 0
    assert 2020 not in by and by[2019]["equity"] is None


def test_parse_legal_extracts_sheet_fields_and_document_metadata_without_tokens():
    payload = {"capital": 7000, "numero_tva_intracommunautaire": "FR61492892195", "numero_rcs": "492 892 195 R.C.S. Marseille",
               "greffe": "MARSEILLE", "statut_rcs": "Inscrit", "date_immatriculation_rcs": "2006-11-21", "objet_social": "PLATRERIE",
               "formes_exercice": ["Artisanale non réglementée", "Artisanale réglementée"], "date_cloture_exercice": "31 Décembre",
               "prochaine_date_cloture_exercice": "2026-12-31", "opposition_utilisation_commerciale": True,
               "conventions_collectives": [{"nom": "Bâtiment", "idcc": "2420"}],
               "derniers_statuts": {"date_acte": "2020-01-02", "disponible": True, "token": "SECRET", "type": "Statuts"},
               "comptes": [{"date_cloture": "2025-12-31", "disponible": True, "type_comptes": "C", "token": "SECRET"}],
               "depots_actes": [{"date_depot": "2022-05-05", "disponible": False, "actes": [{"type": "Modification"}], "token": "SECRET"}],
               "extrait_immatriculation": {"token": "SECRET"}}
    legal, docs = pappers.parse_legal(payload)
    assert legal["share_capital_eur"] == 7000 and legal["vat_number"] == "FR61492892195" and legal["rcs_registry"] == "MARSEILLE"
    assert legal["exercise_forms"] == "Artisanale non réglementée, Artisanale réglementée" and legal["collective_agreements"] == "Bâtiment (IDCC 2420)"
    assert legal["commercial_opposition"] is True and str(legal["next_closing_date"]) == "2026-12-31"
    assert [d["kind"] for d in docs] == ["statutes", "accounts", "act", "registration_extract"]
    assert "SECRET" not in str(docs) and docs[2]["label"] == "Modification" and docs[2]["available"] is False
