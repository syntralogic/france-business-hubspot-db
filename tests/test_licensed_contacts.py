import pytest

from fbdb.enrich.licensed_contacts import DEFAULT_MAPPING, LicenceError, parse_row, run

# Fictional values. 55210055400025 is Luhn-valid (check with is_valid_siret in the same way as other tests).


def test_parse_row_normalizes_and_reports_invalid(monkeypatch):
    import fbdb.enrich.licensed_contacts as m
    monkeypatch.setattr(m, "is_valid_siret", lambda v: len(v) == 14)
    monkeypatch.setattr(m, "is_valid_siren", lambda v: len(v) == 9)
    row = {"siret": "552 100 554 00025", "phone": "01 23 45 67 89", "phone_2": "123", "email": "Contact@Exemple.FR",
           "website": "https://www.Exemple.fr/accueil", "linkedin": "https://facebook.com/x"}
    siret, siren, contacts, issues = parse_row(row, DEFAULT_MAPPING)
    assert siret == "55210055400025" and siren == "552100554"
    assert ("phone", "01 23 45 67 89", "+33123456789") in contacts
    assert ("email", "Contact@Exemple.FR", "contact@exemple.fr") in contacts
    assert ("website", "https://www.Exemple.fr/accueil", "exemple.fr") in contacts
    codes = [c for c, _ in issues]
    assert "INVALID_PHONE" in codes and "INVALID_LINKEDIN" in codes


def test_parse_row_dedups_same_phone_in_two_columns(monkeypatch):
    import fbdb.enrich.licensed_contacts as m
    monkeypatch.setattr(m, "is_valid_siren", lambda v: True)
    row = {"siren": "552100554", "phone": "01 23 45 67 89", "phone_2": "+33 1 23 45 67 89"}
    _, _, contacts, _ = parse_row(row, DEFAULT_MAPPING)
    assert [c[0] for c in contacts] == ["phone"]


def test_licence_gate_blocks_without_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("LICENSED_CONTACTS_CONTRACT_REF", raising=False)
    f = tmp_path / "x.csv"
    f.write_text("siret,phone\n", encoding="utf-8")
    with pytest.raises(LicenceError):
        run("postgresql://unused", f, None)
