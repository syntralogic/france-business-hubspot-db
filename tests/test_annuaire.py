from fbdb.enrich.annuaire import map_dirigeants

# Shape verified against recherche-entreprises.api.gouv.fr on 13 Oct 2026 (values here are fictional).
RESULT = {
    "siren": "552100554",
    "dirigeants": [
        {"nom": "dupont", "prenoms": "Jean-Paul, Marie, Luc", "annee_de_naissance": "1958",
         "date_de_naissance": "1958-08", "qualite": "Gérant", "nationalite": None, "type_dirigeant": "personne physique"},
        {"siren": "377652938", "denomination": "AUDITEX", "qualite": "Commissaire aux comptes suppléant",
         "type_dirigeant": "personne morale"},
        {"nom": "Muller", "prenoms": "", "annee_de_naissance": None, "qualite": "Président",
         "type_dirigeant": "personne physique"},
        {"nom": "Dupont", "prenoms": "JEAN-PAUL", "annee_de_naissance": "1958", "qualite": "Gérant",
         "type_dirigeant": "personne physique"},
    ],
}


def test_keeps_natural_persons_only_and_normalizes():
    rows = map_dirigeants(RESULT)
    assert rows == [
        {"last_name": "DUPONT", "first_name": "Jean-Paul", "role": "Gérant", "birth_year": 1958},
        {"last_name": "MULLER", "first_name": "", "role": "Président", "birth_year": None},
    ]


def test_only_birth_year_is_kept():
    assert all("date_de_naissance" not in r for r in map_dirigeants(RESULT))


def test_empty_or_missing_dirigeants():
    assert map_dirigeants({}) == []
    assert map_dirigeants({"dirigeants": None}) == []
    assert map_dirigeants({"dirigeants": [{"type_dirigeant": "personne physique", "nom": " "}]}) == []


def test_invalid_birth_year_is_dropped():
    r = {"dirigeants": [{"nom": "X", "prenoms": "Y", "annee_de_naissance": "19", "type_dirigeant": "personne physique"}]}
    assert map_dirigeants(r)[0]["birth_year"] is None
