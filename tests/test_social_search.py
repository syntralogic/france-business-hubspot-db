"""Social accounts by web search: URL parsing, proof rules and the search flow, with a fake Firecrawl client (no network, no database)."""
import httpx

from fbdb.enrich import social_search as s
from fbdb.enrich import website as w

SIREN = "552100554"
SIREN_TXT = "SIREN 552 100 554"
FACTS = {"siren": SIREN, "phones": {"+33472000000"}, "emails": {"contact@dupont.fr"}, "domains": {"dupont.fr"},
         "street": "12 RUE DES LILAS", "postal_code": "69003", "city": "LYON 3EME"}


def facts(**over):
    return {**FACTS, **over}


# ------------------------------------------------------------ queries
def test_search_name_drops_legal_form_and_prefers_trading_name():
    assert s.search_name("DUPONT ELEC SARL", None) == "DUPONT ELEC"
    assert s.search_name("DUPONT ELEC SARL", "Dupont Electricité") == "Dupont Electricité"
    assert s.search_name("SAS", None) == "SAS"            # never an empty query


def test_build_query_shapes():
    assert s.build_query("facebook", "Dupont Elec", "Lyon") == 'site:facebook.com "Dupont Elec" "Lyon"'
    assert s.build_query("instagram", "Dupont Elec", "Lyon") == 'site:instagram.com "Dupont Elec" "Lyon"'
    assert s.build_query("tiktok", "Dupont Elec", "Lyon") == 'site:tiktok.com "Dupont Elec" "Lyon"'
    assert s.build_query("youtube", "Dupont Elec", "Lyon") == 'site:youtube.com "Dupont Elec" "Lyon"'
    assert s.build_query("linkedin_company", "Dupont Elec", "Lyon") == 'site:linkedin.com/company "Dupont Elec" "Lyon"'
    assert s.build_query("linkedin_director", "Dupont Elec", "Lyon", ("Eva", "Durand")) == 'site:linkedin.com/in "Eva Durand" "Dupont Elec"'


# ------------------------------------------------------------ profile URLs
def test_profile_url_accepts_profiles_only():
    ok = {
        ("facebook", "https://www.facebook.com/DupontElec/about"): "https://www.facebook.com/DupontElec",
        ("facebook", "https://fr-fr.facebook.com/dupont.elec?ref=x"): "https://www.facebook.com/dupont.elec",
        ("facebook", "https://m.facebook.com/pages/Dupont-Elec/123456"): "https://www.facebook.com/pages/Dupont-Elec/123456",
        ("facebook", "https://www.facebook.com/profile.php?id=1000123"): "https://www.facebook.com/profile.php?id=1000123",
        ("instagram", "https://www.instagram.com/dupont_elec/?hl=fr"): "https://www.instagram.com/dupont_elec",
        ("tiktok", "https://www.tiktok.com/@dupontelec/video/7"): "https://www.tiktok.com/@dupontelec",
        ("linkedin_company", "https://fr.linkedin.com/company/dupont-elec?trk=x"): "https://www.linkedin.com/company/dupont-elec",
        ("linkedin_company", "https://www.linkedin.com/school/ecole-x/"): "https://www.linkedin.com/school/ecole-x",
        ("linkedin_director", "https://fr.linkedin.com/in/eva-durand-123"): "https://www.linkedin.com/in/eva-durand-123",
        ("youtube", "https://www.youtube.com/@dupontelec/videos"): "https://www.youtube.com/@dupontelec",
        ("youtube", "https://www.youtube.com/channel/UC123"): "https://www.youtube.com/channel/UC123",
    }
    for (network, url), want in ok.items():
        assert s.profile_url(network, url) == want, (network, url)
    no = [("facebook", "https://www.facebook.com/sharer/sharer.php?u=x"), ("facebook", "https://www.facebook.com/groups/12"),
          ("facebook", "https://www.facebook.com/profile.php"), ("facebook", "https://www.facebook.com/"),
          ("facebook", "https://www.facebook.com/people/Jean-Dupont/100"), ("facebook", "https://example.com/dupont"),
          ("instagram", "https://www.instagram.com/p/AbC123/"), ("instagram", "https://www.instagram.com/explore/tags/x"),
          ("tiktok", "https://www.tiktok.com/tag/elec"), ("tiktok", "https://www.tiktok.com/@"),
          ("linkedin_company", "https://www.linkedin.com/in/eva"), ("linkedin_director", "https://www.linkedin.com/company/x"),
          ("youtube", "https://www.youtube.com/watch?v=abc"), ("youtube", "https://www.youtube.com/results?search_query=x"),
          ("facebook", "not a url")]
    for network, url in no:
        assert s.profile_url(network, url) is None, (network, url)


def test_candidates_dedupe_profiles_and_keep_search_order():
    hits = [{"url": "https://www.facebook.com/dupont/posts/1"}, {"url": "https://www.facebook.com/Dupont"},
            {"url": "https://www.facebook.com/other"}, {"url": "https://www.facebook.com/third"}, {"url": "https://www.facebook.com/4th"}]
    got = [u for u, _ in s.candidates("facebook", hits, limit=3)]
    assert got == ["https://www.facebook.com/dupont", "https://www.facebook.com/other", "https://www.facebook.com/third"]
    assert s.candidates("facebook", []) == []


# ------------------------------------------------------------ proof
def test_street_words_are_the_significant_words_only():
    assert s.street_words("12 RUE DES LILAS") == ["lilas"]
    assert s.street_words("3 AV DU MARECHAL FOCH") == ["marechal", "foch"]
    assert s.street_words("ZI LES PRES") == ["pres"]
    assert s.street_words(None) == [] and s.street_words("1 RUE DE LA") == []


def test_evidence_siren_beats_everything_and_must_be_ours():
    ev = s.find_evidence(f"Dupont Elec - {SIREN_TXT} - Lyon", [], FACTS)
    assert ev["type"] == "siren_on_page" and "552 100 554" in ev["passage"]
    assert s.find_evidence("SIREN 401 234 562", [], FACTS) is None            # somebody else's SIREN


def test_evidence_website_link_needs_the_proven_domain_in_a_link_not_a_lookalike():
    assert s.find_evidence("x", ["https://www.dupont.fr/contact"], FACTS)["type"] == "website_linked"
    for fake in ("https://notdupont.fr", "https://dupont.fr.evil.com", "https://dupont.free.fr", "https://dupont-fr.com"):
        assert s.find_evidence("x", [fake], FACTS) is None, fake
    assert s.find_evidence("visitez dupont.fr", [], FACTS) is None            # written in the text only: not enough
    assert s.find_evidence("x", ["https://dupont.fr"], facts(domains=set())) is None


def test_evidence_phone_and_email_must_be_values_we_already_hold():
    ev = s.find_evidence("Appelez-nous: 04 72 00 00 00", [], FACTS)
    assert ev["type"] == "phone_match" and "04 72 00 00 00" in ev["passage"]
    assert s.find_evidence("Tel 04 72 11 11 11", [], FACTS) is None
    assert s.find_evidence("", ["tel:+33472000000"], FACTS)["type"] == "phone_match"
    assert s.find_evidence("ecrire a contact@dupont.fr", [], FACTS)["type"] == "email_match"
    assert s.find_evidence("ecrire a autre@dupont.fr", [], FACTS) is None


def test_evidence_address_needs_street_postal_code_and_city():
    page = "Dupont Elec\n12 rue des Lilas\n69003 Lyon"
    assert s.find_evidence(page, [], FACTS)["type"] == "address_match"
    assert s.find_evidence("69003 Lyon", [], FACTS) is None                        # a town alone proves nothing
    assert s.find_evidence("rue des Lilas 69100 Villeurbanne", [], FACTS) is None   # other postal code / city
    assert s.find_evidence(page, [], facts(street=None)) is None                    # no street known: no address proof
    assert s.find_evidence(page, [], facts(street="12 RUE DE LA")) is None          # street without a significant word


def test_evidence_for_a_person_requires_both_names_first():
    text = f"Eva Durand - Gerante - {SIREN_TXT}"
    assert s.find_evidence(text, [], FACTS, person=("Eva", "Durand"))["type"] == "siren_on_page"
    assert s.find_evidence(text, [], FACTS, person=("Eva", "Martin")) is None
    assert s.find_evidence(f"Éva DURAND {SIREN_TXT}", [], FACTS, person=("Eva", "Durand")) is not None   # accents / case


# ------------------------------------------------------------ the search flow
class Fake:
    def __init__(self, hits, pages=None):
        self.hits, self.pages, self.searches, self.scrapes = hits, pages or {}, [], []

    def search_hits(self, query, limit=5):
        self.searches.append(query)
        return self.hits

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


FB = "https://www.facebook.com/dupontelec"


def test_proof_in_the_snippet_confirms_without_scraping():
    c = Fake([{"url": FB, "title": "Dupont Elec", "description": "12 rue des Lilas 69003 Lyon. Tel 04 72 00 00 00"}])
    r = s.search_network(c, "facebook", "q", FACTS)
    assert r["status"] == "confirmed" and r["candidate_url"] == FB and r["evidence_type"] == "phone_match" and r["proof_url"] == FB
    assert c.scrapes == []                                     # no extra credit spent


def test_proof_on_the_profile_page_confirms_after_one_scrape():
    c = Fake([{"url": FB + "/about", "title": "Dupont Elec | Facebook", "description": "Electricien"}],
             {FB: {"markdown": "Dupont Elec", "links": ["https://dupont.fr"]}})
    r = s.search_network(c, "facebook", "q", FACTS)
    assert r["status"] == "confirmed" and r["evidence_type"] == "website_linked" and c.scrapes == [FB]


def test_no_proof_stays_to_verify_with_the_snippet_as_justification():
    c = Fake([{"url": FB, "title": "Dupont Elec", "description": "Electricien a Lyon"}], {FB: {"markdown": "Bienvenue", "links": []}})
    r = s.search_network(c, "facebook", "q", FACTS)
    assert r["status"] == "to_verify" and r["candidate_url"] == FB and r["evidence_type"] is None
    assert "Electricien a Lyon" in r["proof"]


def test_unreadable_page_stays_to_verify_and_says_so():
    c = Fake([{"url": FB, "title": "Dupont Elec", "description": ""}])           # scrape returns None (login wall)
    r = s.search_network(c, "facebook", "q", FACTS)
    assert r["status"] == "to_verify" and r["proof"].endswith("profile page not readable")


def test_only_the_first_candidate_is_scraped_but_every_snippet_is_checked():
    other = "https://www.facebook.com/other"
    c = Fake([{"url": FB, "title": "A", "description": ""}, {"url": other, "title": "B", "description": SIREN_TXT}])
    r = s.search_network(c, "facebook", "q", FACTS)
    assert r["status"] == "confirmed" and r["candidate_url"] == other and c.scrapes == []
    c2 = Fake([{"url": FB, "title": "A", "description": ""}, {"url": other, "title": "B", "description": ""}])
    s.search_network(c2, "facebook", "q", FACTS)
    assert c2.scrapes == [FB]


def test_not_found_is_not_the_same_as_inaccessible():
    c = Fake([{"url": "https://www.facebook.com/groups/1", "title": "", "description": ""}])
    r = s.search_network(c, "facebook", "q", FACTS)
    assert r["status"] == "not_found" and r["candidate_url"] is None and c.scrapes == []
    assert s.search_network(Fake([]), "facebook", "q", FACTS)["status"] == "not_found"
    r2 = s.search_network(Fake(None), "facebook", "q", FACTS)
    assert r2["status"] == "inaccessible" and r2["candidate_url"] is None


def test_director_profile_needs_the_name_on_the_snippet_or_page():
    li = "https://www.linkedin.com/in/eva-durand"
    hit = {"url": "https://fr.linkedin.com/in/eva-durand", "title": "Eva Durand - Gerante - Dupont Elec", "description": SIREN_TXT}
    r = s.search_network(Fake([hit]), "linkedin_director", "q", FACTS, person=("Eva", "Durand"))
    assert r["status"] == "confirmed" and r["candidate_url"] == li
    homonym = {"url": "https://fr.linkedin.com/in/paul-martin", "title": "Paul Martin", "description": SIREN_TXT}
    r2 = s.search_network(Fake([homonym]), "linkedin_director", "q", FACTS, person=("Eva", "Durand"))
    assert r2["status"] == "to_verify"


def test_cap_reached_before_search_runs_nothing_and_before_scrape_keeps_the_candidate():
    c = Fake([{"url": FB, "title": "A", "description": ""}])
    r = s.search_network(c, "facebook", "q", FACTS, reserve=lambda: False)
    assert r["cap_reached"] and r["candidate_url"] is None and c.searches == []
    calls = iter([True, False])                                 # search allowed, scrape refused
    r2 = s.search_network(Fake([{"url": FB, "title": "A", "description": ""}]), "facebook", "q", FACTS, reserve=lambda: next(calls))
    assert r2["cap_reached"] and r2["status"] == "to_verify" and r2["candidate_url"] == FB


# ------------------------------------------------------------ client
def test_search_hits_distinguishes_no_result_from_refused_query():
    def ok(request):
        return httpx.Response(200, json={"success": True, "data": [{"url": "https://a.fr", "title": "A", "description": "d"}, {"title": "x"}]})

    c = w.FirecrawlClient("k", transport=httpx.MockTransport(ok))
    assert c.search_hits("q") == [{"url": "https://a.fr", "title": "A", "description": "d"}]
    assert w.FirecrawlClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"success": True, "data": []}))
                             ).search_hits("q") == []
    refused = w.FirecrawlClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    assert refused.search_hits("q") is None and refused.search("q") == []        # search() keeps its old contract
    failed = w.FirecrawlClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"success": False})))
    assert failed.search_hits("q") is None
    try:
        w.FirecrawlClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(402))).search_hits("q")
        raise AssertionError("expected QuotaExhausted")
    except w.QuotaExhausted:
        pass
