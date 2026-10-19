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
