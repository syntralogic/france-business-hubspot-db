from fbdb.enrich import site_extract as x

PAGE = """
# Contact
Standard : 04 72 00 00 00
Dépannage urgence 24h/24 : 06 12 34 56 78
Siret 552 100 554 00017
Écrivez-nous : contact@alpha-elec.fr — devis : devis@alpha-elec.fr
logo@2x.png  nom@example.com
## Notre équipe
Luc MARTIN
Gérant
Tel direct : 06 98 76 54 32 — luc.martin@alpha-elec.fr

Eva Durand, Responsable commercial
Mentions légales : Directeur de la publication : Luc MARTIN
"""


def test_phones_normalised_deduped_with_use():
    phones = {p["normalized"]: p for p in x.extract_phones(PAGE, ["tel:+33612345678", "tel:0611223344"])}
    assert set(phones) == {"+33472000000", "+33612345678", "+33698765432", "+33611223344"}
    assert phones["+33472000000"]["use"] == "switchboard"
    assert phones["+33612345678"]["use"] == "emergency"
    assert "552100554" not in " ".join(phones)           # a SIRET is not a phone


def test_emails_filter_junk_and_guess_use():
    emails = {e["normalized"]: e["use"] for e in x.extract_emails(PAGE, ["mailto:compta@alpha-elec.fr?subject=x"])}
    assert emails == {"contact@alpha-elec.fr": "general", "devis@alpha-elec.fr": "sales",
                      "luc.martin@alpha-elec.fr": None, "compta@alpha-elec.fr": "accounting"}


def test_people_from_team_block_and_legal_notice():
    people = {(p["first_name"], p["last_name"]): p for p in x.extract_people(PAGE)}
    assert set(people) == {("Luc", "Martin"), ("Eva", "Durand")}
    luc, eva = people[("Luc", "Martin")], people[("Eva", "Durand")]
    assert luc["function"].lower() == "gérant"
    assert luc["phones"] == ["+33698765432"] and luc["emails"] == ["luc.martin@alpha-elec.fr"]
    assert eva["function"] == "Responsable commercial" and eva["phones"] == [] and eva["emails"] == []


def test_headings_and_job_titles_are_not_names():
    text = "Notre Equipe\nResponsable Commercial\nChef Chantier\nContact Gérant\nMentions Légales"
    assert x.extract_people(text) == []


def test_legal_notice_alone_gives_a_person():
    p = x.extract_people("Directeur de la publication : Mme Sophie LEROY-BLANC")
    assert [(a["first_name"], a["last_name"], a["rule"]) for a in p] == [("Sophie", "Leroy-Blanc", "legal_notice")]


def test_shared_value_is_not_attached_to_two_people():
    text = "Luc Martin, Gérant\nEva Durand, Responsable commercial\nStandard 04 72 00 00 00"
    p = x.extract_people(text)
    assert all(a["phones"] == [] for a in p)


def test_two_names_on_one_line_are_ambiguous_and_skipped():
    assert x.extract_people("Luc Martin et Eva Durand, gérants") == []


def test_contact_pages_same_site_best_first():
    links = ["/nos-realisations", "/equipe", "https://other.fr/contact", "https://www.alpha.fr/contact#form", "/"]
    assert x.contact_pages("https://alpha.fr", links) == ["https://www.alpha.fr/contact", "https://alpha.fr/equipe"]


def test_contact_line_belongs_to_the_person_above_not_the_next_one():
    text = "Luc MARTIN\nGérant\nDirect : 06 98 76 54 32\nEva Durand, Responsable commercial\nSAV : 06 11 22 33 44"
    p = {a["last_name"]: a for a in x.extract_people(text)}
    assert p["Martin"]["phones"] == ["+33698765432"] and p["Durand"]["phones"] == []


# ---------------------------------------------------------------- people: false-positive classes found by stress-testing typical page texts
def _who(text):
    return [(p["first_name"], p["last_name"], p["function"]) for p in x.extract_people(text)]


def test_headings_products_towns_and_brands_are_not_people():
    assert _who("Chauffage Climatisation\nResponsable Installation Dépannage\nContact") == []
    assert _who("Directeur régional Saint Etienne") == [] and _who("Responsable Aix Provence\nSaint Denis Directeur") == []
    assert _who("Gérant de la SCI Les Pins") == []
    assert _who("Pompe à chaleur Air Eau Responsable Garantie") == []
    assert _who("Atlantic Climatisation Responsable Qualité") == []


def test_real_surnames_that_are_also_nouns_stay_allowed():
    assert _who("Paul Charpentier - Chef de chantier") == [("Paul", "Charpentier", "Chef de chantier")]
    assert _who("Luc Boulanger, gérant") == [("Luc", "Boulanger", "gérant")]


def test_credits_authors_testimonials_and_partners_are_not_the_companys_team():
    assert _who("Hébergeur : OVH SAS, directeur Octave Klaba") == []
    assert _who("Article écrit par Julien Moreau, responsable communication") == []
    assert _who("« Très bon travail » Paul Durand, gérant de la boulangerie Dupont") == []
    assert _who("Notre partenaire Daikin, Directeur Marc Leroy chez Daikin France") == []
    assert _who("« Super » dit Léa Martin\nGérant\n") == []              # a testimonial line next to a function is not its owner
    # the legal notice formula stays read even when a credit follows on the same line
    assert _who("Directeur de la publication : Jean Dupont - Conception : agence Web") == [("Jean", "Dupont", "Directeur de la publication")]


def test_civility_does_not_swallow_the_first_name():
    assert _who("Mme Claire Petit, assistante de direction") == [("Claire", "Petit", "assistante de direction")]
    assert _who("Directeur : M. Pierre Lambert") == [("Pierre", "Lambert", "Directeur")]


def test_a_rejected_pair_does_not_hide_the_name_that_follows_it():
    assert _who("Directeur Marc Leroy") == [("Marc", "Leroy", "Directeur")]
    assert _who("Notre Gérant Jean Dupont") == [("Jean", "Dupont", "Gérant")]
    assert _who("Directeur Marc Leroy chez Dupont Elec") == []         # two names on the line: ambiguous, nobody


def test_function_text_never_contains_the_persons_own_name_or_a_trailing_preposition():
    assert x._tidy_function("Directeur Marc Leroy chez", "Marc", "Leroy") == "Directeur"
    assert x._tidy_function("Responsable commercial de", "", "") == "Responsable commercial"
