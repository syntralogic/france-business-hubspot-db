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
