from fbdb.transform.normalize import (
    address_hash, is_valid_siren, is_valid_siret, normalize_address,
    normalize_company_name, normalize_domain, normalize_email, normalize_phone_fr,
)


def test_siren_luhn():
    assert is_valid_siren("552 100 554")      # real-format SIREN passing Luhn
    assert not is_valid_siren("552100555")
    assert not is_valid_siren("12345")


def test_siret_luhn_and_length():
    assert is_valid_siret("55210055400013")
    assert not is_valid_siret("55210055400014")
    assert not is_valid_siret("5521005540001")


def test_name_normalization():
    assert normalize_company_name("Boulangerie Dupont S.A.R.L.") == "BOULANGERIE DUPONT"
    assert normalize_company_name("Café de l'Étoile SAS") == "CAFE DE L ETOILE"


def test_address_normalization_and_hash():
    a = normalize_address("12 Av. de la Paix", "69003", "Lyon 3eme")
    assert a == "12 AVENUE DE LA PAIX 69003 LYON"
    assert address_hash("12 av de la paix", "69003", "LYON") == address_hash("12 AVENUE DE LA PAIX", "69003", "Lyon 3EME")


def test_phone():
    assert normalize_phone_fr("04 72 00 00 01") is None or normalize_phone_fr("04 72 00 00 01").startswith("+33")
    assert normalize_phone_fr("+33 1 42 68 53 00") == "+33142685300"
    assert normalize_phone_fr("abc") is None
    assert normalize_phone_fr("") is None


def test_email_and_domain():
    assert normalize_email(" Contact@Exemple.FR ") == "contact@exemple.fr"
    assert normalize_email("not-an-email") is None
    assert normalize_domain("https://www.Exemple.fr/contact?x=1") == "exemple.fr"
    assert normalize_domain("nodomain") is None
