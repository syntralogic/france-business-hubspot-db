import httpx
import pytest

from fbdb.enrich import email_find as ef


def client(handler):
    return ef.HunterFinder("k", transport=httpx.MockTransport(handler))


def test_find_parses_answer():
    def h(req):
        assert req.url.params["domain"] == "alpha.fr" and req.url.params["first_name"] == "Luc"
        return httpx.Response(200, json={"data": {"email": "luc.martin@alpha.fr", "score": 91, "sources": [{}, {}],
                                                  "verification": {"status": "valid"}}})
    assert client(h).find("Luc", "Martin", "alpha.fr") == {"email": "luc.martin@alpha.fr", "score": 91, "status": "valid", "sources": 2}


def test_find_no_address_is_none():
    assert client(lambda r: httpx.Response(200, json={"data": {"email": None}})).find("A", "B", "x.fr") is None
    assert client(lambda r: httpx.Response(404)).find("A", "B", "x.fr") is None


def test_find_quota_stops():
    with pytest.raises(ef.QuotaExhausted):
        client(lambda r: httpx.Response(402)).find("A", "B", "x.fr")


def test_unknown_category_refused():
    with pytest.raises(ValueError, match="unknown categories"):
        ef.run("postgresql://x", client=object(), categories=("ceo",))
