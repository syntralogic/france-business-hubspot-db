"""Press (spec section 12, part 3): candidate filtering, mention line, proof levels, credit use. Fake client, no network, no database."""
from fbdb.enrich import press as p

FACTS = {"siren": "552100554", "phones": {"+33472000000"}, "emails": set(), "domains": {"alpha.fr"},
         "street": "12 RUE DES LILAS", "postal_code": "69003", "city": "LYON"}
NAME = "Alpha Elec"
ART = "https://www.leprogres.fr/economie/alpha-elec-inaugure"
ART2 = "https://www.lyonmag.com/article/alpha-elec"
ART3 = "https://www.actu.fr/rhone/alpha-elec"


def hit(url, title="Alpha Elec", desc=""):
    return {"url": url, "title": title, "description": desc}


class Fake:
    def __init__(self, hits, pages=None):
        self.hits, self.pages, self.searches, self.scrapes = hits, pages or {}, [], []

    def search_hits(self, query, limit=5):
        self.searches.append((query, limit))
        return self.hits

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


# ------------------------------------------------------------ query and mention
def test_query_and_mention_line():
    assert p.build_query("Alpha Elec", "LYON 3E") == '"Alpha Elec" "LYON 3E"'
    assert p.mention_line("Intro.\nL'entreprise ALPHA-ELEC a inauguré son atelier. Suite.", "Alpha Elec") == "L'entreprise ALPHA-ELEC a inauguré son atelier."
    assert p.mention_line("Alphabet Electrique", "Alpha Elec") is None            # whole words only
    assert p.mention_line("texte", "ab") is None and p.mention_line("", NAME) is None


# ------------------------------------------------------------ candidates
def test_candidates_drop_own_site_social_directories_jobs_and_unnamed_results():
    hits = [hit("https://alpha.fr/actualites/x"), hit("https://www.facebook.com/alphaelec"), hit("https://www.societe.com/societe/alpha-elec-552100554.html"),
            hit("https://fr.indeed.com/cmp/Alpha-Elec"), hit("https://www.pagesjaunes.fr/pros/1"),
            hit("https://www.autre.fr/x", title="Une autre entreprise", desc="sans nom"),
            hit(ART), hit(ART + "/"), hit(ART2, title="Reportage", desc="Reportage chez Alpha Elec à Lyon."), hit("ftp://x.fr/a")]
    got = p.candidates(hits, NAME, {"alpha.fr"})
    assert [c["url"] for c in got] == [ART, ART2] and got[0]["publisher"] == "leprogres.fr"
    assert got[1]["passage"] == "Reportage chez Alpha Elec à Lyon." and got[0]["passage"] == "Alpha Elec"
    assert len(p.candidates([hit(f"https://p{i}.fr/a") for i in range(9)], NAME, set())) == p.MAX_CANDIDATES


def test_a_subdomain_of_a_directory_or_of_the_own_site_is_dropped():
    assert p.candidates([hit("https://blog.alpha.fr/x"), hit("https://fr.linkedin.com/company/alpha")], NAME, {"alpha.fr"}) == []


# ------------------------------------------------------------ proof
def test_proof_in_the_snippet_confirms_without_scraping():
    c = Fake([hit(ART, desc="Alpha Elec, SIREN 552 100 554, a inauguré son atelier.")])
    r = p.search_press(c, NAME, "q", FACTS)
    a = r["articles"][0]
    assert r["status"] == "confirmed" and a["status"] == "confirmed" and a["evidence_type"] == "siren_on_page" and "552 100 554" in a["proof"]
    assert c.scrapes == [] and c.searches == [("q", p.SEARCH_LIMIT)]


def test_proof_on_the_page_confirms_after_a_scrape_and_a_link_to_the_proven_site_counts():
    c = Fake([hit(ART, desc="Reportage")], {ART: {"markdown": "Alpha Elec à Lyon", "links": ["https://alpha.fr/contact"]}})
    r = p.search_press(c, NAME, "q", FACTS)
    assert r["status"] == "confirmed" and r["articles"][0]["evidence_type"] == "website_linked" and c.scrapes == [ART]


def test_phone_on_the_page_is_a_proof_but_a_homonym_page_is_not():
    ok = Fake([hit(ART)], {ART: {"markdown": "Alpha Elec - tel 04 72 00 00 00", "links": []}})
    assert p.search_press(ok, NAME, "q", FACTS)["articles"][0]["evidence_type"] == "phone_match"
    homonym = Fake([hit(ART)], {ART: {"markdown": "Alpha Elec à Marseille, tel 04 91 00 00 00, 13 rue de la Paix 13001 Marseille", "links": []}})
    r = p.search_press(homonym, NAME, "q", FACTS)
    assert r["status"] == "to_verify" and r["articles"][0]["status"] == "to_verify" and r["articles"][0]["proof"] is None


def test_no_proof_stays_to_verify_and_an_unreadable_page_does_not_confirm():
    r = p.search_press(Fake([hit(ART)], {}), NAME, "q", FACTS)
    assert r["status"] == "to_verify" and r["articles"][0]["evidence_type"] is None


def test_at_most_two_pages_are_scraped_and_a_proven_snippet_costs_nothing():
    hits = [hit(ART), hit(ART2), hit(ART3, desc="SIREN 552100554")]
    c = Fake(hits)
    r = p.search_press(c, NAME, "q", FACTS)
    assert c.scrapes == [ART, ART2]                                  # ART3 is proven by its snippet: never scraped, 2 scrapes max
    assert [a["status"] for a in r["articles"]] == ["to_verify", "to_verify", "confirmed"] and r["status"] == "confirmed"


def test_not_found_inaccessible_and_the_cap():
    assert p.search_press(Fake([]), NAME, "q", FACTS)["status"] == "not_found"
    assert p.search_press(Fake([hit("https://www.facebook.com/x")]), NAME, "q", FACTS)["status"] == "not_found"    # only non-press results
    assert p.search_press(Fake(None), NAME, "q", FACTS)["status"] == "inaccessible"
    c = Fake([hit(ART)])
    r = p.search_press(c, NAME, "q", FACTS, reserve=lambda: False)
    assert r["cap_reached"] and r["articles"] == [] and c.searches == []
    calls = iter([True, False])                                      # search allowed, scrape refused
    r2 = p.search_press(c, NAME, "q", FACTS, reserve=lambda: next(calls))
    assert r2["cap_reached"] and r2["status"] == "to_verify" and c.scrapes == []
