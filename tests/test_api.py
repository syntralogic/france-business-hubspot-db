from fbdb import api


def test_empty_params_defaults():
    where, values, page, per_page = api.build_query({})
    assert where == "" and values == [] and (page, per_page) == (1, 25)


def test_siret_siren_and_name_search():
    w, v, *_ = api.build_query({"q": ["552 100 554"]})
    assert "v.siren = %s" in w and v == ["552100554"]
    w, v, *_ = api.build_query({"q": "55210055400013"})
    assert "v.siret = %s" in w and v == ["55210055400013"]
    w, v, *_ = api.build_query({"q": "boulang"})
    assert w.count("ILIKE") == 3 and v == ["%boulang%"] * 3


def test_like_wildcards_are_escaped_and_never_concatenated_into_sql():
    w, v, *_ = api.build_query({"q": "50%_off'; DROP TABLE companies;--"})
    assert "DROP" not in w and "\\%" in v[0] and "\\_" in v[0]


def test_filters_and_paging_limits():
    w, v, page, per_page = api.build_query({"dept": "2a", "city": "Lyon", "rge": "1", "page": "3", "per_page": "9999"})
    assert v == ["2A", "%Lyon%"] and "rge_labels" in w and (page, per_page) == (3, api.MAX_PER_PAGE)
    assert api.build_query({"page": "abc"})[2:] == (1, 25)
    assert api.build_query({"page": "-4", "per_page": "0"})[2:] == (1, 1)


def test_no_personal_columns_exposed():
    for banned in ("phone", "email", "director", "linkedin", "google"):
        assert banned not in api.COLUMNS
    assert "is_sole_proprietor" in api.FROM
