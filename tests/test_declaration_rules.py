"""Declared service area and team size (spec sections 2 and 4): pure rules, no network, no database."""
from fbdb.enrich import declaration_rules as d
from fbdb.enrich import site_activity as sa

URL = "https://dupont.fr"


def got(md, url=URL):
    return {(i["kind"], i["value"]): i for i in d.extract_declarations(md, url)}


def test_places_from_an_intervention_sentence_and_a_zone_line_keep_the_passage():
    g = got("Nous intervenons à Lyon, Villeurbanne et Bron ainsi que dans le Rhône (69).\n"
            "Zone d'intervention : Paris, Hauts-de-Seine (92) et Val-de-Marne.\n")
    places = {v for (k, v) in g if k == "service_area_place"}
    assert places == {"Lyon", "Villeurbanne", "Bron", "Rhône (69)", "Paris", "Hauts-de-Seine (92)", "Val-de-Marne"}
    lyon = g[("service_area_place", "Lyon")]
    assert "Nous intervenons à Lyon" in lyon["passage"] and lyon["url"] == URL and lyon["number"] is None


def test_radius_and_scope_are_declared_only_when_written():
    g = got("Nous intervenons dans un rayon de 30 km autour de Rennes.\nNous nous déplaçons partout en France pour vos chantiers.\n"
            "Intervention sur toute la région.\n")
    assert g[("service_area_radius_km", "30 km autour de Rennes")]["number"] == 30
    assert ("service_area_scope", "national") in g and ("service_area_scope", "regional") in g
    # no intervention wording around "France" -> not a mobility claim
    assert got("Fabriqué en France, partout en France nos clients nous aiment.") == {}


def test_negation_hiring_and_lowercase_words_are_not_places():
    g = got("Nous n'intervenons pas à Marseille.\nNous recrutons : nous intervenons à Nantes.\n"
            "Nous intervenons chez les particuliers et chez les professionnels.\nNous intervenons sur toute la France.\n")
    assert [k for k in g if k[0] == "service_area_place"] == []        # nothing but a scope
    assert ("service_area_scope", "national") in g


def test_team_size_exact_qualified_and_number_words():
    assert got("Une équipe de douze personnes à votre service.")[("team_size", "12")]["qualifier"] == "exact"
    g = got("Notre équipe compte plus de 15 techniciens qualifiés.")[("team_size", "15")]
    assert g["number"] == 15 and g["qualifier"] == "plus_de"
    assert got("Environ 8 collaborateurs.")[("team_size", "8")]["qualifier"] == "environ"


def test_team_size_not_read_from_experience_hiring_partners_or_client_staff():
    for text in ["Plus de 20 ans d'expérience en plomberie.", "Nous recrutons une équipe de 5 techniciens.",
                 "Nous travaillons avec une équipe de 4 sous-traitants.", "Formation de vos salariés : 10 salariés par session.",
                 "Nos 3 000 clients nous font confiance."]:
        assert [k for k in got(text) if k[0] == "team_size"] == [], text


def test_one_team_size_per_page_and_per_read_first_wins():
    assert len([k for k in got("Une équipe de 5 personnes.\nUne équipe de 9 personnes.\n") if k[0] == "team_size"]) == 1
    merged = d.merge_declarations([d.extract_declarations("Équipe de 5 personnes.", "u1"),
                                   d.extract_declarations("Équipe de 9 personnes. Nous intervenons à Lyon.", "u2")])
    assert [(m["kind"], m["value"], m["url"]) for m in merged] == [("team_size", "5", "u1"), ("service_area_place", "Lyon", "u2")]


def test_images_are_never_a_declaration():
    assert got("![Nous intervenons à Paris avec une équipe de 12 personnes](x.jpg)") == {}


def test_read_site_returns_declarations_and_counts_them_as_extracted():
    class Fake:
        def __init__(self, pages):
            self.pages = pages

        def scrape(self, url):
            return self.pages.get(url)

    c = Fake({URL: {"markdown": "Nous intervenons à Lyon et Bron.", "links": ["/contact-us"]}})
    res = sa.read_site(c, URL)
    assert res["outcome"] == "extracted" and res["items"] == []          # no activity claim, but a declaration is something found
    assert {x["value"] for x in res["declarations"]} == {"Lyon", "Bron"}
