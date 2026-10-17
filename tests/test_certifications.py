import pytest

from fbdb.enrich import certifications as c

M = c.DEFAULT_MAPPING
SIREN, SIRET = "552100554", "55210055400005"


def test_normalize_cert_type():
    assert c.normalize_cert_type("QUALIBAT") == "Qualibat"
    assert c.normalize_cert_type("Qualit'EnR") == "Qualit'EnR"
    assert c.normalize_cert_type("Assurance décennale") == "Decennale"
    assert c.normalize_cert_type("RC Pro") == "RC pro"
    assert c.normalize_cert_type("ISO 9001") is None


def test_parse_row_ok_and_dates():
    siret, siren, cert, err = c.parse_row({"siret": SIRET, "cert_type": "Qualifelec", "label": "IRVE", "valid_from": "01/02/2024",
                                           "valid_to": "2027-02-01"}, M)
    assert (siret, siren, err) == (SIRET, SIREN, None)
    assert cert["type"] == "Qualifelec" and str(cert["valid_from"]) == "2024-02-01" and str(cert["valid_to"]) == "2027-02-01"


def test_parse_row_rejects():
    assert c.parse_row({"siret": "12345678901234", "cert_type": "Qualibat"}, M)[3][0] == "INVALID_SIRET"
    assert c.parse_row({"cert_type": "Qualibat"}, M)[3][0] == "NO_VALID_ID"
    assert c.parse_row({"siren": SIREN, "cert_type": "ISO"}, M)[3][0] == "UNKNOWN_CERT_TYPE"
    assert c.parse_row({"siren": SIREN, "cert_type": "Qualibat", "valid_to": "nope"}, M)[3][0] == "BAD_DATE"
    assert c.parse_row({"siren": SIREN, "cert_type": "Qualibat", "valid_from": "2025-01-01", "valid_to": "2024-01-01"}, M)[3][0] == "BAD_DATE"


def test_licence_gate(monkeypatch):
    monkeypatch.delenv("CERTIFICATIONS_CONTRACT_REF", raising=False)
    with pytest.raises(c.LicenceError):
        c.load("postgresql://x", "f.csv", None)
