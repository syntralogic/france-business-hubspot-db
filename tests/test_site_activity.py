"""Site activity (spec section 3): classification rules, page choice and the read flow, with a fake Firecrawl client (no network, no database)."""
from fbdb.enrich import activity_rules as r
from fbdb.enrich import site_activity as sa

URL = "https://dupont.fr"


def found(md, url=URL):
    return {(i["facet"], i["code"]): i for i in r.extract_activities(md, url)}


# ------------------------------------------------------------ rules: what is read
def test_each_facet_is_found_with_its_passage_url_and_matched_words():
    md = ("# Dupont Electricité\n"
          "Électricien à Lyon, nous installons des bornes de recharge IRVE pour particuliers et copropriétés.\n"
          "Neuf et rénovation, dépannage 24h/24, contrat d'entretien annuel.\n"
          "Installateur agréé Daikin, qualifié RGE et Qualifelec.\n")
    got = found(md)
    for key in [("trade", "electricien"), ("specialty", "irve"), ("service", "installation"), ("client_type", "particuliers"),
                ("client_type", "syndics_copropriete"), ("work_type", "neuf"), ("work_type", "renovation"), ("work_type", "depannage"),
                ("contract", "astreinte_urgence"), ("contract", "contrat_entretien"), ("brand", "daikin"),
                ("approval", "brand_agreement:daikin"), ("approval", "rge"), ("approval", "qualifelec")]:
        assert key in got, key
    irve = got[("specialty", "irve")]
    assert irve["url"] == URL and irve["matched_text"].lower() == "bornes de recharge"   # leftmost match and "bornes de recharge IRVE" in irve["passage"]
    assert "Installateur agréé Daikin" in got[("approval", "brand_agreement:daikin")]["passage"]
    assert got[("work_type", "neuf")]["matched_text"].lower() == "neuf et rénovation"


def test_accents_case_and_menu_lines_are_handled():
    got = found("ÉLECTRICITÉ GÉNÉRALE | Plomberie | Climatisation | Pompe à chaleur")
    assert {("trade", "electricien"), ("trade", "plombier"), ("specialty", "climatisation"), ("specialty", "pompe_a_chaleur")} <= set(got)
    assert got[("trade", "plombier")]["passage"] == "ÉLECTRICITÉ GÉNÉRALE | Plomberie | Climatisation | Pompe à chaleur"


def test_photos_and_their_alt_text_are_never_a_proof():
    assert found("![Borne IRVE installée à Lyon par notre équipe](chantier.jpg)") == {}
    assert found("![Daikin](logo.png) ![Chantier photovoltaique](p.jpg)") == {}
    # the same words as text are read; the link text is kept, its URL is not
    got = found("[Nos bornes IRVE](https://dupont.fr/irve-climatisation)")
    assert ("specialty", "irve") in got and ("specialty", "climatisation") not in got


def test_negation_right_before_the_keyword_cancels_it_but_a_claim_does_not():
    assert found("Nous ne faisons pas de climatisation.") == {}
    assert found("Nous ne faisons pas de plomberie ni de climatisation.") == {}
    assert found("Plus de dépannage le week-end, désolé.") == {}
    assert ("specialty", "climatisation") in found("Plus de 20 ans d'expérience en climatisation.")
    assert ("specialty", "climatisation") in found("Nous ne faisons pas de plomberie mais nous posons de la climatisation.")


def test_hiring_partner_and_training_sentences_are_not_claims():
    assert found("Nous recrutons un électricien en CDI.") == {}
    assert found("Nous travaillons avec un plombier partenaire.") == {}
    assert found("Organisme de formation IRVE pour électriciens.") == {}
    # a partner sentence still counts for a brand / approval claim (that is how it is written)
    got = found("Partenaire officiel Daikin.")
    assert ("brand", "daikin") in got and ("approval", "brand_agreement:daikin") in got and ("trade", "electricien") not in got


def test_brand_without_agreement_word_is_only_a_brand():
    got = found("Nous posons du matériel Atlantic et Hager.")
    assert ("brand", "atlantic") in got and ("brand", "hager") in got
    assert not any(k[0] == "approval" for k in got)


def test_first_sentence_wins_per_claim_and_long_passages_are_cut_around_the_match():
    got = found("Climatisation pour les maisons. Climatisation pour les bureaux.")
    assert got[("specialty", "climatisation")]["passage"] == "Climatisation pour les maisons."
    long_line = "mot " * 120 + "installation de bornes IRVE " + "fin " * 120
    p = found(long_line)[("specialty", "irve")]["passage"]
    assert len(p) <= r.MAX_PASSAGE + 6 and "IRVE" in p


def test_renovation_and_neuf_are_not_confused():
    assert ("work_type", "neuf") not in found("Remise à neuf de votre salle de bain.")
    assert ("work_type", "renovation") in found("Remise à neuf de votre salle de bain.")
    assert ("work_type", "neuf") in found("Installations neuves et travaux neufs.")


def test_merge_pages_keeps_the_first_page_that_states_a_claim():
    a = r.extract_activities("Électricien à Lyon.", "https://x.fr")
    b = r.extract_activities("Électricien. Climatisation.", "https://x.fr/services")
    merged = {(i["facet"], i["code"]): i["url"] for i in r.merge_pages([a, b])}
    assert merged[("trade", "electricien")] == "https://x.fr" and merged[("specialty", "climatisation")] == "https://x.fr/services"


def test_empty_and_noise_pages_give_nothing():
    assert r.extract_activities("", URL) == [] and r.extract_activities(None, URL) == []
    assert r.extract_activities("Bienvenue sur notre site. Contactez-nous.", URL) == []


# ------------------------------------------------------------ pages
def test_activity_pages_pick_offer_pages_and_skip_blog_legal_hiring_files_and_other_sites():
    links = ["/services", "/prestations/electricite", "/blog/choisir-sa-borne-irve", "/mentions-legales", "/recrutement", "/contact",
             "https://autre.fr/services", "/a-propos", "/plaquette.pdf", "/depannage-urgence", "#top", "/", "/services?x=1", "/nos-metiers"]
    got = sa.activity_pages(URL, links)
    # best hint first: "prestation" outranks "service", which outranks "metier"; /depannage-urgence and /a-propos fall beyond the limit of 3
    assert got == ["https://dupont.fr/prestations/electricite", "https://dupont.fr/services", "https://dupont.fr/nos-metiers"]
    assert sa.activity_pages(URL, links, limit=1) == ["https://dupont.fr/prestations/electricite"]
    assert sa.activity_pages(URL, []) == [] and sa.activity_pages(URL, None) == []


# ------------------------------------------------------------ read flow
class Fake:
    def __init__(self, pages):
        self.pages, self.scrapes = pages, []

    def scrape(self, url):
        self.scrapes.append(url)
        return self.pages.get(url)


def test_read_site_merges_home_and_offer_pages_home_first():
    c = Fake({URL: {"markdown": "Électricien à Lyon.", "links": ["/services", "/blog/x"]},
              URL + "/services": {"markdown": "Électricien. IRVE et climatisation.", "links": []}})
    res = sa.read_site(c, URL)
    assert res["outcome"] == "extracted" and res["pages_read"] == 2 and c.scrapes == [URL, URL + "/services"]
    by = {(i["facet"], i["code"]): i["url"] for i in res["items"]}
    assert by[("trade", "electricien")] == URL and by[("specialty", "irve")] == URL + "/services"


def test_read_site_nothing_found_is_not_inaccessible():
    res = sa.read_site(Fake({URL: {"markdown": "Bienvenue.", "links": []}}), URL)
    assert res["outcome"] == "nothing_found" and res["items"] == [] and res["pages_read"] == 1
    res2 = sa.read_site(Fake({}), URL)
    assert res2["outcome"] == "inaccessible" and res2["pages_read"] == 0


def test_read_site_unreadable_offer_page_does_not_stop_the_others():
    c = Fake({URL: {"markdown": "x", "links": ["/services", "/prestations"]}, URL + "/prestations": {"markdown": "Plomberie.", "links": []}})
    res = sa.read_site(c, URL)
    assert res["outcome"] == "extracted" and res["pages_read"] == 2


def test_read_site_respects_the_cap_before_each_scrape():
    c = Fake({URL: {"markdown": "Électricien.", "links": ["/services"]}})
    assert sa.read_site(c, URL, reserve=lambda: False)["cap_reached"] and c.scrapes == []
    calls = iter([True, False])                        # home allowed, offer page refused
    res = sa.read_site(c, URL, reserve=lambda: next(calls))
    assert res["cap_reached"] and res["outcome"] == "extracted" and c.scrapes == [URL]


def test_at_most_four_pages_per_site():
    links = [f"/services-{i}" for i in range(10)]
    pages = {URL: {"markdown": "x", "links": links}}
    pages.update({f"{URL}/services-{i}": {"markdown": "Plomberie.", "links": []} for i in range(10)})
    c = Fake(pages)
    sa.read_site(c, URL)
    assert len(c.scrapes) == sa.MAX_PAGES == 4


def test_building_types_and_commitments_are_claims_with_their_passage():
    md = ("Nous intervenons dans des maisons individuelles, des copropriétés et des bureaux.\n"
          "Devis gratuit sous 48h. Garantie décennale sur tous nos travaux.\n"
          "Notre équipe vous accompagne de A à Z. Spécialisés en bornes de recharge.\n")
    got = {(i["facet"], i["code"]): i for i in r.extract_activities(md, "https://x.fr/")}
    for key in [("building_type", "maison_individuelle"), ("building_type", "immeuble_collectif"), ("building_type", "bureaux"),
                ("commitment", "delai"), ("commitment", "devis_gratuit"), ("commitment", "garantie"),
                ("commitment", "accompagnement"), ("commitment", "specialisation")]:
        assert key in got, key
    assert "Garantie décennale" in got[("commitment", "garantie")]["passage"]
    assert got[("building_type", "bureaux")]["url"] == "https://x.fr/"


def test_building_type_and_commitment_ignore_negation_hiring_and_photos():
    md = ("Nous ne travaillons pas sur les commerces.\n"
          "Recrutement : nous cherchons un électricien pour nos magasins, devis gratuit pour les candidats.\n"
          "![chantier dans une usine avec garantie](photo.jpg)\n")
    assert [i for i in r.extract_activities(md, "u") if i["facet"] in ("building_type", "commitment")] == []
