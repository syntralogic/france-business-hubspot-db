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
