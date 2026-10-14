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
