from datetime import date

from fbdb.enrich.bodacc import fetch_day, parse_record, parse_siren


def test_parse_siren_variants():
    assert parse_siren(["552 100 554", "552100554"]) == "552100554"
    assert parse_siren("552100554") == "552100554"
    assert parse_siren(["12 34"]) == "" and parse_siren(None) == ""


def test_parse_record_ok_and_rejects():
    r = parse_record({"id": "B202600001", "registre": ["552 100 554"], "dateparution": "2026-10-08", "familleavis": "Collective",
                      "familleavis_lib": "Procédures collectives", "tribunal": "TC Paris", "numerodepartement": "75"})
    assert r["siren"] == "552100554" and r["family"] == "collective" and r["published_on"] == date(2026, 10, 8)
    assert parse_record({"id": "X", "registre": [], "dateparution": "2026-10-08"}) is None
    assert parse_record({"id": "X", "registre": ["552100554"], "dateparution": "bad"}) is None


def test_fetch_day_pages_until_total():
    calls = []

    def fake(params):
        calls.append(params["offset"])
        n = 100 if params["offset"] == 0 else 20
        return {"total_count": 120, "results": [{"id": f"{params['offset']}-{i}"} for i in range(n)]}

    rows = list(fetch_day(date(2026, 10, 8), "collective", fake))
    assert len(rows) == 120 and calls == [0, 100]


def test_fetch_day_empty():
    assert list(fetch_day(date(2026, 10, 8), "radiation", lambda p: {"total_count": 0, "results": []})) == []
