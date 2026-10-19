from fbdb.enrich.annuaire import map_legal_entity_officers
from fbdb.enrich.register_events import classify_notice


def test_classify_notice():
    assert classify_notice("modification", "Modifications diverses", "Ouverture d'un établissement secondaire") == "bodacc_establishment"
    assert classify_notice("creation", "Créations", "Création d'établissement") == "bodacc_establishment"
    assert classify_notice("modification", "Modification", "Modification de l'activité") == "bodacc_activity"
    assert classify_notice("modification", "Modifications diverses", "Changement de dirigeant") is None
    assert classify_notice("modification", "Modifications", "Fermeture d'un établissement") is None      # closing is not an opening
    assert classify_notice("collective", "Procédures collectives", "Ouverture d'une procédure, activité") is None
    assert classify_notice("radiation", "Radiations", "Création d'établissement") is None
    assert classify_notice(None, None, None) is None


def test_map_legal_entity_officers():
    res = {"siren": "552100554", "dirigeants": [
        {"type_dirigeant": "personne morale", "siren": "552 081 317", "denomination": "HOLDING  X", "qualite": "Président"},
        {"type_dirigeant": "personne morale", "siren": "552081317", "denomination": "HOLDING X", "qualite": "Président"},      # duplicate
        {"type_dirigeant": "personne morale", "siren": "552081317", "denomination": "HOLDING X", "qualite": "Gérant"},         # other role
        {"type_dirigeant": "personne morale", "siren": "123456789", "denomination": "BAD LUHN", "qualite": "Président"},
        {"type_dirigeant": "personne morale", "siren": "552100554", "denomination": "SELF", "qualite": "Président"},
        {"type_dirigeant": "personne morale", "siren": "775665912", "denomination": "AUDIT SA", "qualite": "Commissaire aux comptes titulaire"},
        {"type_dirigeant": "personne physique", "nom": "Dupont", "prenoms": "Jean", "qualite": "Gérant"},
        {"type_dirigeant": "personne morale", "denomination": "NO SIREN", "qualite": "Président"},
    ]}
    out = map_legal_entity_officers(res)
    assert out == [{"siren": "552081317", "name": "HOLDING X", "role": "Président"},
                   {"siren": "552081317", "name": "HOLDING X", "role": "Gérant"}]
    assert map_legal_entity_officers({}) == []


def test_provider_uses_and_rge_url(tmp_path):
    from fbdb.enrich.ademe_rge import read_rows
    from fbdb.enrich.licensed_contacts import provider_uses
    m = {"emails": ["Mail devis", "Mail direction", "Mail autre"],
         "email_uses": {"Mail devis": "sales", "Mail direction": "DIRECTION", "Mail autre": "boss"}}
    row = {"Mail devis": "Devis@X.fr", "Mail direction": "pdg@x.fr", "Mail autre": "a@x.fr"}
    uses, issues = provider_uses(row, m)
    assert uses == {"devis@x.fr": "sales", "pdg@x.fr": "direction"} and issues == [("INVALID_EMAIL_USE", "Mail autre -> boss")]
    assert provider_uses({"Mail devis": "not an email"}, m)[0] == {}
    assert provider_uses(row, {"emails": ["Mail devis"]}) == ({}, [])
    f = tmp_path / "r.csv"
    f.write_text("siret;nom_qualification;date_debut;date_fin;url_qualification\n"
                 "55210055400005;QualiPAC;2025-01-01;2027-01-01;https://x.fr/c/1\n"
                 "55210055400005;Autre;2025-01-01;2027-01-01;javascript:alert(1)\n", encoding="utf-8")
    rows = list(read_rows(f))
    assert rows[0]["url"] == "https://x.fr/c/1" and rows[1]["url"] is None
