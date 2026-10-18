import httpx

from fbdb.enrich import website as w
from fbdb.enrich import website_discovery as d

SIREN = "552100554"
SIREN_TXT = f"SIREN {SIREN[:3]} {SIREN[3:6]} {SIREN[6:]}"


def test_build_query_prefers_trading_name_and_quotes_it():
    assert d.build_query("DUPONT ELEC SARL", "Dupont Electricité", "Lyon") == '"Dupont Electricité" Lyon'
    assert d.build_query("DUPONT ELEC SARL", None, "Lyon") == '"DUPONT ELEC SARL" Lyon'
    assert d.build_query("X", None, None) == '"X"'


def test_blocked_hosts_are_whole_label_matches():
    for u in ["https://www.facebook.com/dupont", "https://fr.linkedin.com/company/x", "https://www.societe.com/societe/x",
              "https://maps.google.fr/x", "https://www.google.com/search", "https://annuaire-entreprises.data.gouv.fr/e/1",
              "https://www.pagesjaunes.fr/pros/1", "not a url"]:
        assert d.is_blocked(u), u
    for u in ["https://dupont-elec.fr", "https://mygoogle.fr", "https://www.xavier-plomberie.com", "https://fbx.com"]:
        assert not d.is_blocked(u), u


def test_candidate_sites_dedupe_hosts_skip_blocked_and_return_home_pages():
    res = ["https://www.pagesjaunes.fr/x", "https://dupont.fr/contact?a=1", "https://www.dupont.fr/autre", "https://autre.fr/page/2",
           "https://third.fr"]
    assert d.candidate_sites(res, 2) == ["https://dupont.fr", "https://autre.fr"]
    assert d.candidate_sites([], 2) == []


def test_proof_must_be_on_home_or_legal_page():
    site = "https://dupont.fr"
    assert d.proof_is_acceptable(site, "https://dupont.fr")
    assert d.proof_is_acceptable(site, "https://dupont.fr/mentions-legales")
    assert d.proof_is_acceptable(site, "https://dupont.fr/contact")
    assert not d.proof_is_acceptable(site, "https://dupont.fr/nos-partenaires")
    assert not d.proof_is_acceptable(site, None)


class FakeClient:
    def __init__(self, results, pages):
        self.results, self.pages, self.searches, self.scrapes = results, pages, [], []

    def search(self, query, limit=5):
        self.searches.append(query)
        return self.results

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


def test_discover_proven_on_second_candidate():
    c = FakeClient(["https://wrong.fr/a", "https://dupont.fr/b"], {
        "https://wrong.fr": {"markdown": "SIREN 123 456 782", "links": []},
        "https://dupont.fr": {"markdown": "Accueil", "links": ["/mentions-legales", "https://facebook.com/dupont"]},
        "https://dupont.fr/mentions-legales": {"markdown": SIREN_TXT, "links": []},
    })
    r = d.discover(c, SIREN, "q")
    assert r["outcome"] == "proven" and r["site_url"] == "https://dupont.fr" and r["checked"] == 2
    assert r["result"]["socials"] == {"facebook": "https://facebook.com/dupont"}


def test_discover_siren_on_a_partner_page_is_not_a_proof():
    c = FakeClient(["https://dupont.fr"], {
        "https://dupont.fr": {"markdown": "Accueil", "links": ["/nos-partenaires/"]},
    })
    # the partner page is not even visited (not a legal hint), so nothing is proven
    assert d.discover(c, SIREN, "q")["outcome"] == "not_proven"
    c2 = FakeClient(["https://dupont.fr"], {"https://dupont.fr": {"markdown": f"Partenaire: {SIREN_TXT}", "links": []}})
    # proof on the home page is accepted; proof elsewhere is rejected by proof_is_acceptable (unit-tested above)
    assert d.discover(c2, SIREN, "q")["outcome"] == "proven"


def test_discover_no_results_and_blocked_only_results():
    assert d.discover(FakeClient([], {}), SIREN, "q")["outcome"] == "no_results"
    c = FakeClient(["https://www.societe.com/x", "https://www.facebook.com/y"], {})
    r = d.discover(c, SIREN, "q")
    assert r["outcome"] == "no_results" and c.scrapes == []       # nothing scraped (and paid) for directories


def test_discover_stops_when_cap_reached_and_does_not_record_a_result():
    c = FakeClient(["https://dupont.fr"], {})
    r = d.discover(c, SIREN, "q", reserve=lambda: False)
    assert r["cap_reached"] and c.searches == []                  # no search when the cap is already reached
    calls = iter([True, False])                                   # search allowed, first scrape refused
    c2 = FakeClient(["https://dupont.fr"], {})
    r2 = d.discover(c2, SIREN, "q", reserve=lambda: next(calls))
    assert r2["cap_reached"] and r2["outcome"] == "not_proven"


def test_firecrawl_search_parses_results_and_handles_errors():
    def handler(request):
        assert request.url.path == "/v1/search"
        body = request.read().decode()
        assert '"lang": "fr"' in body or '"lang":"fr"' in body
        return httpx.Response(200, json={"success": True, "data": [{"url": "https://a.fr", "title": "A"}, {"title": "no url"}]})

    c = w.FirecrawlClient(api_key="k", transport=httpx.MockTransport(handler))
    assert c.search("q") == ["https://a.fr"]

    c402 = w.FirecrawlClient(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(402)))
    try:
        c402.search("q")
        raise AssertionError("expected QuotaExhausted")
    except w.QuotaExhausted:
        pass
    c404 = w.FirecrawlClient(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert c404.search("q") == []
