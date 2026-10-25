from fbdb.enrich.directors import annuaire_finances


def test_annuaire_finances_keeps_real_figures_and_drops_unpublished_zero_revenue():
    rows = annuaire_finances({"finances": {"2020": {"ca": 0, "resultat_net": 36345}, "2021": {"ca": 120000, "resultat_net": -500},
                                           "x": {"ca": 1}, "2022": {"ca": None, "resultat_net": None}}})
    by = {r["year"]: r for r in rows}
    assert set(by) == {2020, 2021}
    assert by[2020]["revenue"] is None and by[2020]["net_result"] == 36345
    assert by[2021]["revenue"] == 120000 and by[2021]["net_result"] == -500
    assert annuaire_finances({"finances": None}) == [] and annuaire_finances({}) == []
