from fbdb.enrich.geocode import build_csv, parse_result

OUT = """id,street,postcode,city,latitude,longitude,result_label,result_score,result_status
1,1 rue de la Paix,75002,Paris,48.869,2.331,1 Rue de la Paix 75002 Paris,0.97,ok
2,xx,69001,Lyon,45.76,4.83,Lyon,0.45,ok
3,zzz,00000,Nowhere,,,,,not-found
4,bad,13001,Marseille,43.29,5.37,Marseille,abc,ok
"""


def test_build_csv_quotes_and_header():
    t = build_csv([{"id": 7, "street": '3 rue "X", bis', "postal_code": "75001", "city": "Paris"}])
    assert t.splitlines()[0] == "id,street,postcode,city"
    assert '"3 rue ""X"", bis"' in t


def test_threshold_and_status():
    r = {x["id"]: x for x in parse_result(OUT, 0.6)}
    assert r[1]["ok"] and r[1]["lat"] == 48.869 and r[1]["reason"] == ""
    assert not r[2]["ok"] and r[2]["reason"] == "LOW_SCORE"
    assert not r[3]["ok"] and r[3]["reason"] == "NOT_FOUND" and r[3]["lat"] is None
    assert not r[4]["ok"]  # unparsable score -> 0 -> flagged


def test_lower_threshold_accepts_borderline():
    assert {x["id"]: x for x in parse_result(OUT, 0.4)}[2]["ok"]


TYPED = """id,street,postcode,city,latitude,longitude,result_label,result_score,result_status,result_type
1,1 rue de la Paix,75002,Paris,48.869,2.331,1 Rue de la Paix 75002 Paris,0.97,ok,housenumber
2,Le Bourg,38000,Quelquepart,45.1,5.7,38000 Quelquepart,0.80,ok,municipality
3,Rue Longue,69001,Lyon,45.76,4.83,Rue Longue 69001 Lyon,0.70,ok,street
"""


def test_town_centre_match_is_not_verified_and_precision_is_kept():
    r = {x["id"]: x for x in parse_result(TYPED, 0.6)}
    assert r[1]["ok"] and r[1]["precision"] == "housenumber"
    assert not r[2]["ok"] and r[2]["reason"] == "MUNICIPALITY_ONLY" and r[2]["lat"] == 45.1 and r[2]["precision"] == "municipality"
    assert r[3]["ok"] and r[3]["precision"] == "street"
    # a low score keeps its own reason even for a municipality answer
    assert {x["id"]: x for x in parse_result(TYPED, 0.9)}[2]["reason"] == "LOW_SCORE"


def test_missing_result_type_keeps_the_old_rule():
    r = {x["id"]: x for x in parse_result(OUT, 0.6)}
    assert r[1]["ok"] and r[1]["precision"] is None
