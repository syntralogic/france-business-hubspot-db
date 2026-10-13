from fbdb.match.dedup import (
    AUTO_MATCH, DISTINCT, REVIEW, SAME_COMPANY_OTHER_ESTABLISHMENT, Record, compare,
)


def test_same_siret_matches():
    d = compare(Record(siret="55210055400013"), Record(siret="55210055400013"))
    assert d.outcome == AUTO_MATCH


def test_same_siren_other_siret_is_other_establishment():
    d = compare(Record(siret="55210055400013"), Record(siret="55210055400021"))
    assert d.outcome == SAME_COMPANY_OTHER_ESTABLISHMENT


def test_different_siren_same_name_is_distinct():
    a = Record(name="Boulangerie Dupont", siren="552100554")
    b = Record(name="Boulangerie Dupont", siren="732829320")
    assert compare(a, b).outcome == DISTINCT


def test_fuzzy_match_with_phone():
    a = Record(name="Boulangerie Dupont SARL", phone="+33142685300")
    b = Record(name="BOULANGERIE DUPONT", phone="01 42 68 53 00")
    assert compare(a, b).outcome == AUTO_MATCH


def test_same_address_different_name_is_distinct():
    a = Record(name="Plomberie Martin", street="5 rue Victor Hugo", postal_code="75001", city="Paris")
    b = Record(name="Cabinet Leroy Avocats", street="5 rue Victor Hugo", postal_code="75001", city="Paris")
    assert compare(a, b).outcome == DISTINCT


def test_same_name_far_away_is_distinct():
    a = Record(name="Garage Durand", postal_code="13001", city="Marseille")
    b = Record(name="Garage Durand", postal_code="59000", city="Lille")
    assert compare(a, b).outcome == DISTINCT


def test_similar_name_same_postal_goes_to_review():
    a = Record(name="Garage Durand", postal_code="13001", city="Marseille")
    b = Record(name="Garage Durand Auto", postal_code="13001", city="Marseille")
    assert compare(a, b).outcome == REVIEW
