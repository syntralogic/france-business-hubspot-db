import httpx
import pytest

from fbdb.enrich import email_verify as ev
from fbdb.enrich import phone_lookup as pl


def test_email_decide():
    assert ev.decide("valid") == ("verified", "valid")
    assert ev.decide("invalid") == ("unavailable", "invalid")
    assert ev.decide("disposable") == ("unavailable", "disposable")
    assert ev.decide("accept_all") == (None, "accept_all")
    assert ev.decide(None) == (None, "unknown")


def test_hunter_client():
    def handler(req):
        e = req.url.params["email"]
        if e.startswith("quota"):
            return httpx.Response(402)
        return httpx.Response(200, json={"data": {"status": "valid"}})
    c = ev.HunterClient("k", transport=httpx.MockTransport(handler))
    assert c.verify("a@b.fr") == "valid"
    with pytest.raises(ev.QuotaExhausted):
        c.verify("quota@b.fr")


def test_parse_lookup():
    assert pl.parse_lookup({"valid": True, "line_type_intelligence": {"type": "mobile"}}) == (True, "mobile")
    assert pl.parse_lookup({"valid": True, "line_type_intelligence": {"type": "nonFixedVoip"}}) == (True, "voip")
    assert pl.parse_lookup({"valid": True, "line_type_intelligence": {"type": "weird"}}) == (True, "other")
    assert pl.parse_lookup({"valid": False}) == (False, None)
    assert pl.parse_lookup({"valid": True}) == (True, None)


def test_twilio_client():
    def handler(req):
        if req.url.path.endswith("+33100000000"):
            return httpx.Response(404)
        if req.url.path.endswith("+33999999999"):
            return httpx.Response(401)
        return httpx.Response(200, json={"valid": True, "line_type_intelligence": {"type": "landline"}})
    c = pl.TwilioLookupClient("sid", "tok", transport=httpx.MockTransport(handler))
    assert c.lookup("+33142000000")["valid"] is True
    assert c.lookup("+33100000000") == {"valid": False}
    with pytest.raises(pl.QuotaExhausted):
        c.lookup("+33999999999")


def test_normalize_contact():
    from fbdb.common.provenance import normalize_contact
    assert normalize_contact("Jean@X.fr") == ("email", "jean@x.fr")
    assert normalize_contact("01 42 00 00 00")[0] == "phone"
    assert normalize_contact("xyz") is None
