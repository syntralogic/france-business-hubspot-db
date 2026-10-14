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
