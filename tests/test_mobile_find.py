import httpx
import pytest

from fbdb.enrich import mobile_find as mf


def client(handler):
    return mf.MobileProvider("k", transport=httpx.MockTransport(handler))


def answer(phones, domain="alpha.fr", charged=True):
    return {"contact": {"data": {"phoneNumbers": phones, "company": {"domain": domain}}, "isCreditCharged": charged}}


def test_find_sends_name_and_domain_asks_phones_only_and_parses_types_and_dnc():
    def h(req):
        p = req.url.params
        assert (p["firstName"], p["lastName"], p["companyDomain"], p["revealPhones"]) == ("Luc", "Martin", "alpha.fr", "true")
        assert req.headers["api_key"] == "k" and "k" not in str(req.url)             # the key goes in a header, never in the URL
        return httpx.Response(200, json=answer([{"internationalNumber": "+33 6 12 34 56 78", "type": "Mobile", "doNotCall": False, "updateDate": "2026-01-02"},
                                                {"number": "+33472000000", "type": "Phone", "doNotCall": True}]))
    got = client(h).find("Luc", "Martin", "alpha.fr")
    assert got["company_domain"] == "alpha.fr" and got["charged"] is True
    assert got["phones"] == [{"number": "+33 6 12 34 56 78", "type": "mobile", "do_not_call": False, "updated": "2026-01-02"},
                             {"number": "+33472000000", "type": "phone", "do_not_call": True, "updated": None}]


def test_find_no_person_or_no_phone_is_none():
    assert client(lambda r: httpx.Response(404)).find("A", "B", "x.fr") is None
    assert client(lambda r: httpx.Response(200, json=answer([]))).find("A", "B", "x.fr") is None
    assert client(lambda r: httpx.Response(200, json={})).find("A", "B", "x.fr") is None


@pytest.mark.parametrize("status", [401, 402, 403])
def test_find_key_or_credit_problem_stops(status):
    with pytest.raises(mf.QuotaExhausted):
        client(lambda r: httpx.Response(status)).find("A", "B", "x.fr")


def test_domain_normalisation():
    assert mf._domain("https://www.Alpha.fr/contact") == "alpha.fr" and mf._domain(None) is None


def test_licence_gate_and_unknown_category(monkeypatch):
    monkeypatch.delenv("LUSHA_CONTRACT_REF", raising=False)
    with pytest.raises(RuntimeError, match="licence gate"):
        mf.run("postgresql://x", client=object())
    with pytest.raises(ValueError, match="unknown categories"):
        mf.run("postgresql://x", client=object(), categories=("ceo",), contract_ref="C-1")
