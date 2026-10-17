import httpx

from fbdb.enrich import website as w
from fbdb.transform.normalize import is_valid_siren

SIREN = "552100554"
assert is_valid_siren(SIREN)


def test_find_sirens_formats_and_luhn():
    text = "Societe X, SIREN 552 100 554, RCS Paris. Autre: 123 456 789 (invalide). SIRET 552 100 554 00055"
    assert w.find_sirens(text) == {SIREN}
    assert w.find_sirens("552.100.554") == {SIREN}
    assert w.find_sirens("tel 0612345678 et 1552100554") == set()      # digits glued to other digits are not a SIREN
    assert w.find_sirens("") == set()


def test_proof_sentence_is_a_short_excerpt():
    p = w.proof_sentence("Mentions legales.\nSIREN : 552 100 554\nHebergeur: OVH", SIREN, width=15)
    assert "552 100 554" in p and "\n" not in p and len(p) < 60
    assert w.proof_sentence("rien", SIREN) is None


def test_social_links_skip_share_and_generic():
    links = ["https://www.facebook.com/sharer/sharer.php?u=x", "https://www.facebook.com/boulangerie.dupont/?ref=a",
             "https://instagram.com/dupont_pain", "https://www.linkedin.com/company/dupont-sa", "https://www.linkedin.com/feed",
             "https://www.youtube.com/watch?v=abc", "https://www.youtube.com/@dupont", "https://twitter.com/intent/tweet",
             "https://www.tiktok.com/@dupont", "https://example.com/facebook.com/x"]
    s = w.social_links(links)
    assert s == {"facebook": "https://www.facebook.com/boulangerie.dupont/", "instagram": "https://instagram.com/dupont_pain",
                 "linkedin": "https://www.linkedin.com/company/dupont-sa", "youtube": "https://www.youtube.com/@dupont",
                 "tiktok": "https://www.tiktok.com/@dupont"}


def test_legal_pages_same_site_ranked():
    base = "https://dupont.fr"
    links = ["/contact", "/mentions-legales", "https://other.com/mentions", "/produits", "https://www.dupont.fr/cgv#top"]
    assert w.legal_pages(base, links) == ["https://dupont.fr/mentions-legales", "https://www.dupont.fr/cgv"][:2]


class Fake:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def scrape(self, url):
        self.calls.append(url)
        return self.pages.get(url)


def test_check_site_proves_on_legal_page_and_collects_socials():
    home = {"markdown": "Bienvenue", "links": ["/mentions-legales", "https://facebook.com/dupont"]}
    legal = {"markdown": f"SIREN {SIREN[:3]} {SIREN[3:6]} {SIREN[6:]}", "links": []}
    c = Fake({"https://dupont.fr": home, "https://dupont.fr/mentions-legales": legal})
    r = w.check_site(c, SIREN, "https://dupont.fr")
    assert r["proven"] and r["proof_url"].endswith("/mentions-legales") and r["pages"] == 2
    assert r["socials"] == {"facebook": "https://facebook.com/dupont"}


def test_check_site_wrong_siren_is_not_proven_and_respects_cap():
    c = Fake({"https://x.fr": {"markdown": "SIREN 123 456 782", "links": ["/mentions"]}})
    r = w.check_site(c, SIREN, "https://x.fr")
    assert not r["proven"] and r["socials"] == {}
    c2 = Fake({"https://x.fr": {"markdown": "", "links": []}})
    r2 = w.check_site(c2, SIREN, "https://x.fr", reserve=lambda: False)
    assert r2["cap_reached"] and c2.calls == []


def test_firecrawl_client_status_handling():
    def handler(req):
        if "bad" in req.read().decode():
            return httpx.Response(402)
        if "gone" in req.read().decode():
            return httpx.Response(404)
        return httpx.Response(200, json={"success": True, "data": {"markdown": "m", "links": ["l"]}})
    cl = w.FirecrawlClient("k", transport=httpx.MockTransport(handler))
    assert cl.scrape("https://ok.fr") == {"markdown": "m", "links": ["l"]}
    assert cl.scrape("https://gone.fr") is None
    try:
        cl.scrape("https://bad.fr")
        assert False
    except w.QuotaExhausted:
        pass
