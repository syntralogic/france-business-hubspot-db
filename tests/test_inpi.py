from fbdb.enrich.inpi import extract_director_surnames, extract_radiation_date

NOTICE = {"formality": {"content": {"personneMorale": {"composition": {"pouvoirs": [
    {"roleEntreprise": "73", "individu": {"descriptionPersonne": {"nom": "Dupont-Léger", "prenoms": ["Anne"]}}},
    {"roleEntreprise": "30", "entreprise": {"denomination": "HOLDING SA"}},
    {"individu": {"descPersonne": {"nom": "Martin"}}},
]}}}}}


def test_director_surnames_normalized_and_companies_skipped():
    assert extract_director_surnames(NOTICE) == {"DUPONT LEGER", "MARTIN"}
    assert extract_director_surnames({}) == set()


def test_radiation_date():
    assert extract_radiation_date({"a": {"dateRadiation": "2026-09-30T00:00:00"}}) == "2026-09-30"
    assert extract_radiation_date({"a": {"dateRadiation": ""}, "b": {"dateCessationActivite": " 2026-01-02 "}}) == "2026-01-02"
    assert extract_radiation_date(NOTICE) is None
