"""Site signals (spec section 12, part 2): realisations, partners, recruitment. Rules, page choice, read flow with a fake Firecrawl client."""
from fbdb.enrich import signal_rules as r
from fbdb.enrich import site_signals as ss

URL = "https://dupont.fr"


def by(items, kind):
    return [i for i in items if i["kind"] == kind]


# ------------------------------------------------------------ page kind
def test_page_kind_from_the_path():
    assert r.page_kind("https://x.fr/nos-realisations") == "realisation" and r.page_kind("https://x.fr/chantiers/lyon") == "realisation"
    assert r.page_kind("https://x.fr/nos-partenaires") == "partner" and r.page_kind("https://x.fr/ils-nous-font-confiance") == "partner"
    assert r.page_kind("https://x.fr/recrutement") == "hiring" and r.page_kind("https://x.fr/carrieres") == "hiring"
    assert r.page_kind("https://x.fr/services") == "other" and r.page_kind("https://x.fr/") == "other"
    # a careers page that talks about projects is a hiring page
    assert r.page_kind("https://x.fr/rejoignez-nos-projets") == "hiring"


# ------------------------------------------------------------ realisations
def test_realisation_needs_a_kind_of_work_and_something_specific_and_keeps_its_sentence():
    md = ("Rénovation d'une maison de 120 m² à Lyon en 2023, pose d'une pompe à chaleur.\n"
          "Installation de 24 panneaux solaires pour la mairie de Villeurbanne (2022).\n"
          "Nous réalisons tous types de travaux de plomberie.\n"          # marketing, not a project
          "Voir toutes nos réalisations pour les particuliers.\n"         # navigation
          "Rénovation de salle de bain.\n")                               # nothing specific / too short
    got = r.extract_realisations(md, URL + "/realisations")
    assert [i["year_mentioned"] for i in got] == [2023, 2022]
    assert got[0]["passage"].startswith("Rénovation d'une maison de 120 m² à Lyon") and got[0]["url"] == URL + "/realisations"
    assert all(i["kind"] == "realisation" and i["code"].startswith("r:") for i in got)


def test_realisations_are_read_on_realisation_pages_only_and_photos_never():
    md = "Installation de 24 panneaux solaires pour la mairie de Villeurbanne (2022).\n![Chantier IRVE Lyon 2021 pour un client](a.jpg)"
    assert len(by(r.extract_signals(md, URL + "/nos-realisations"), "realisation")) == 1          # the photo line is not read
    assert by(r.extract_signals(md, URL + "/services"), "realisation") == []
    assert by(r.extract_signals(md, URL, "home"), "realisation") == []


def test_realisation_skips_hiring_and_negated_and_is_capped():
    assert r.extract_realisations("Nous recrutons un électricien pour nos chantiers à Lyon en 2024.", URL) == []
    many = "\n".join(f"Installation de {i + 2} panneaux solaires pour un client en 20{10 + i}." for i in range(15))
    assert len(r.extract_realisations(many, URL)) == r.MAX_REALISATIONS


# ------------------------------------------------------------ partners
def test_partner_names_from_heading_lists_and_sentences_with_the_relation_word_of_the_site():
    md = ("## Nos partenaires\n- Daikin\n- [Atlantic](https://atlantic.fr)\n- Voir plus\n"
          "Ils nous font confiance : Mairie de Lyon, Vinci Construction et Bouygues.\n"
          "Nous intervenons en sous-traitance pour Eiffage, Spie.\n")
    got = {i["name"]: i for i in r.extract_partners(md, URL + "/partenaires")}
    assert {"Daikin", "Atlantic", "Mairie de Lyon", "Vinci Construction", "Bouygues", "Eiffage", "Spie"} <= set(got)
    assert "Voir plus" not in got
    assert got["Daikin"]["relation"] == "partner" and got["Bouygues"]["relation"] == "client_reference" and got["Spie"]["relation"] == "subcontracting"
    assert got["Daikin"]["passage"] == "Nos partenaires > Daikin"           # two real lines of the page
    assert "sous-traitance pour Eiffage, Spie" in got["Spie"]["passage"]
    assert all(i["kind"] == "partner" for i in got.values())


def test_a_menu_is_not_a_list_of_partners():
    assert r.extract_partners("[Accueil](/)\n[Partenaires](/p)\n[Réalisations](/r)\n[Contact](/c)\n", URL) == []     # link heading
    assert r.extract_partners("## Clients\nAccueil\nPlomberie\nContact\n", URL) == []                                   # real heading, menu block


def test_logo_bar_images_are_never_partners():
    assert r.extract_partners("## Nos partenaires\n![Daikin](d.png) ![Legrand](l.png)\n", URL) == []


def test_partner_negation_and_duplicates():
    assert r.extract_partners("Nous ne travaillons pas avec Daikin.", URL) == []
    got = r.extract_partners("## Nos partenaires\nDaikin\n\nNos partenaires : Daikin, Atlantic.\n", URL)
    assert sorted(i["name"] for i in got) == ["Atlantic", "Daikin"]


# ------------------------------------------------------------ hiring
def test_hiring_sentence_role_and_contract():
    got = by(r.extract_signals("Nous recrutons un électricien en CDI.", URL, "home"), "hiring")
    assert len(got) == 1 and got[0]["name"] == "Electricien" and got[0]["relation"] == "cdi" and got[0]["code"] == "role:electricien"
    assert got[0]["passage"] == "Nous recrutons un électricien en CDI."


def test_hiring_negations_social_and_slogans_are_not_signals():
    for text in ("Nous ne recrutons pas actuellement.", "Aucune offre d'emploi pour le moment.", "Pas de poste à pourvoir.",
                 "Rejoignez-nous sur Facebook et Instagram.", "Plus d'offre disponible."):
        assert r.extract_hiring(text, URL, "hiring") == [], text
    assert r.extract_hiring("Rejoignez-nous !", URL, "home") == []            # a slogan outside a recruitment page
    assert r.extract_hiring("Électricien H/F", URL, "home") == []             # an H/F line counts on a recruitment page only


def test_job_line_on_a_recruitment_page():
    got = r.extract_hiring("Plombier chauffagiste H/F - CDD\nTechnicien SAV F/H\nChef de chantier H/F en alternance", URL + "/recrutement", "hiring")
    roles = {i["name"]: i["relation"] for i in got}
    assert roles == {"Plombier": "cdd", "Technicien": None, "Chef de chantier": "alternance"}


def test_hiring_is_capped_and_one_per_role():
    text = "\n".join(f"Nous recrutons un {x}." for x in ["électricien", "électricien", "plombier", "technicien", "maçon", "peintre", "carreleur"])
    got = r.extract_hiring(text, URL, "home")
    assert len(got) == r.MAX_HIRING and len({i["code"] for i in got}) == len(got)


def test_merge_pages_first_page_wins_and_caps_per_kind():
    a = r.extract_signals("Nous recrutons un électricien.", URL, "home")
    b = r.extract_signals("Nous recrutons un électricien en CDI.\nNos partenaires : Daikin.", URL + "/recrutement", "hiring")
    m = {(i["kind"], i["code"]): i["url"] for i in r.merge_pages([a, b])}
    assert m[("hiring", "role:electricien")] == URL and m[("partner", "n:daikin")] == URL + "/recrutement"


def test_empty_and_noise():
    assert r.extract_signals("", URL, "home") == [] and r.extract_signals(None, URL, "home") == []
    assert r.extract_signals("Bienvenue sur notre site.", URL, "home") == []


# ------------------------------------------------------------ pages
def test_signal_pages_take_one_of_each_kind_first_and_skip_blog_legal_files_other_sites():
    links = ["/realisations", "/nos-chantiers", "/partenaires", "/recrutement", "/blog/chantier-lyon", "/mentions-legales", "/contact",
             "https://autre.fr/realisations", "/galerie-photos", "/plaquette.pdf", "/services", "#top", "/"]
    got = ss.signal_pages(URL, links)
    assert got == [URL + "/realisations", URL + "/partenaires", URL + "/recrutement"]
    assert ss.signal_pages(URL, links, limit=1) == [URL + "/realisations"]
    assert ss.signal_pages(URL, []) == [] and ss.signal_pages(URL, None) == []


def test_signal_pages_fill_with_the_next_best_when_a_kind_is_missing():
    got = ss.signal_pages(URL, ["/realisations", "/nos-chantiers", "/references", "/projets"])
    assert len(got) == 3 and got[0] == URL + "/realisations"


# ------------------------------------------------------------ read flow
class Fake:
    def __init__(self, pages):
        self.pages, self.scrapes = pages, []

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


def test_read_site_merges_home_and_chosen_pages_home_first():
    c = Fake({URL: {"markdown": "Nous recrutons un électricien.", "links": ["/realisations", "/partenaires", "/blog/x"]},
              URL + "/realisations": {"markdown": "Installation de 24 panneaux solaires pour la mairie de Villeurbanne (2022).", "links": []},
              URL + "/partenaires": {"markdown": "Nos partenaires : Daikin, Atlantic.", "links": []}})
    res = ss.read_site(c, URL)
    assert res["outcome"] == "extracted" and res["pages_read"] == 3 and c.scrapes == [URL, URL + "/realisations", URL + "/partenaires"]
    kinds = sorted((i["kind"], i["url"]) for i in res["items"])
    assert kinds == [("hiring", URL), ("partner", URL + "/partenaires"), ("partner", URL + "/partenaires"), ("realisation", URL + "/realisations")]


def test_read_site_nothing_found_is_not_inaccessible():
    res = ss.read_site(Fake({URL: {"markdown": "Bienvenue.", "links": []}}), URL)
    assert res["outcome"] == "nothing_found" and res["items"] == [] and res["pages_read"] == 1
    assert ss.read_site(Fake({}), URL)["outcome"] == "inaccessible"


def test_read_site_respects_the_cap_before_each_scrape_and_a_dead_page_does_not_stop_the_others():
    c = Fake({URL: {"markdown": "Nous recrutons un électricien.", "links": ["/realisations"]}})
    assert ss.read_site(c, URL, reserve=lambda: False)["cap_reached"] and c.scrapes == []
    calls = iter([True, False])
    res = ss.read_site(c, URL, reserve=lambda: next(calls))
    assert res["cap_reached"] and res["outcome"] == "extracted" and c.scrapes == [URL]
    c2 = Fake({URL: {"markdown": "x", "links": ["/realisations", "/partenaires"]}, URL + "/partenaires": {"markdown": "Nos partenaires : Daikin.", "links": []}})
    res2 = ss.read_site(c2, URL)
    assert res2["outcome"] == "extracted" and res2["pages_read"] == 2


def test_at_most_four_pages_per_site():
    links = [f"/realisations-{i}" for i in range(10)]
    pages = {URL: {"markdown": "x", "links": links}}
    pages.update({f"{URL}/realisations-{i}": {"markdown": "Plomberie.", "links": []} for i in range(10)})
    c = Fake(pages)
    ss.read_site(c, URL)
    assert len(c.scrapes) == ss.MAX_PAGES == 4
