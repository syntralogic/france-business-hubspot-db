"""DECP public contracts: identifier rules, readers (JSON stream / CSV) and row preparation. No database, no network."""
import json
from datetime import date
from decimal import Decimal

import pytest

from fbdb.enrich import decp

SIRET = "55210055400013"          # Luhn-valid SIRET of SIREN 552100554
SIREN = "552100554"


def test_siren_of_only_accepts_valid_published_identifiers():
    assert decp.siren_of("SIRET", SIRET) == SIREN
    assert decp.siren_of("siret", "552 100 554 00013") == SIREN
    assert decp.siren_of(None, SIRET) == SIREN                  # no type but an explicit valid SIRET
    assert decp.siren_of("SIREN", SIREN) == SIREN
    assert decp.siren_of("SIRET", "55210055400014") is None      # fails Luhn: never "repaired"
    assert decp.siren_of("TVA", "FR12345678901") is None
    assert decp.siren_of("HORS-UE", SIRET) is None               # an explicit foreign type is not read as a SIRET
    assert decp.siren_of("SIRET", "") is None and decp.siren_of(None, None) is None


def test_parsers_are_strict_and_lenient_where_the_data_is_known_to_be_messy():
    assert decp.parse_amount("1 250,50") == Decimal("1250.50") and decp.parse_amount(12000) == Decimal("12000")
    assert decp.parse_amount("n/a") is None and decp.parse_amount("") is None and decp.parse_amount(None) is None
    assert decp.parse_date("2025-03-04T10:00:00") == date(2025, 3, 4) and decp.parse_date("04/03/2025") == date(2025, 3, 4)
    assert decp.parse_date("not a date") is None and decp.parse_int("12.0") == 12 and decp.parse_int("x") is None


def market(**over):
    m = {"id": "M1", "uid": "UID1", "acheteur": {"id": "21690123100011", "nom": "Mairie de Lyon"},
         "titulaires": [{"typeIdentifiant": "SIRET", "id": SIRET, "denominationSociale": "ALPHA ELEC"}],
         "objet": "Travaux electriques", "montant": 45000.5, "dateNotification": "2025-01-10", "datePublicationDonnees": "2025-01-20",
         "codeCPV": "45310000", "procedure": "Procédure adaptée", "nature": "Marché", "dureeMois": 12,
         "lieuExecution": {"code": "69123", "typeCode": "Code commune", "nom": "Lyon"}}
    m.update(over)
    return m


def test_market_rows_one_row_per_titulaire_direct_or_wrapped():
    rows = list(decp.market_rows(market(titulaires=[
        {"typeIdentifiant": "SIRET", "id": SIRET, "denominationSociale": "ALPHA"},
        {"titulaire": {"typeIdentifiant": "SIRET", "id": "40123456200017", "denominationSociale": "BETA"}}])))
    assert [r["holder_name"] for r in rows] == ["ALPHA", "BETA"] and all(r["n_holders"] == 2 for r in rows)
    r = rows[0]
    assert (r["uid"], r["market_id"], r["buyer_id"], r["buyer_name"], r["place"]) == ("UID1", "M1", "21690123100011", "Mairie de Lyon", "69123 Lyon")
    assert list(decp.market_rows(market(titulaires=[]))) == [] and list(decp.market_rows({})) == []
    assert len(list(decp.market_rows(market(titulaires={"typeIdentifiant": "SIRET", "id": SIRET})))) == 1


def write_json(tmp_path, payload, name="d.json"):
    p = tmp_path / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return p


def test_json_shapes_are_all_read(tmp_path):
    ms = [market(uid=f"U{i}") for i in range(3)]
    for payload in ({"marches": ms}, {"marches": {"marche": ms}}, ms, {"other": [1, 2], "marches": ms}):
        got = [m["uid"] for m in decp.iter_json_markets(write_json(tmp_path, payload))]
        assert got == ["U0", "U1", "U2"], payload


def test_json_is_streamed_across_chunk_boundaries_and_objects_larger_than_a_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(decp, "CHUNK", 64)                          # every object spans several chunks
    ms = [market(uid=f"U{i}", objet="x" * 300) for i in range(5)]
    assert [m["uid"] for m in decp.iter_json_markets(write_json(tmp_path, {"marches": ms}))] == [f"U{i}" for i in range(5)]
    assert list(decp.iter_json_markets(write_json(tmp_path, {"marches": []}, "e.json"))) == []


def test_json_without_markets_or_truncated_raises_instead_of_silently_loading_nothing(tmp_path):
    with pytest.raises(ValueError):
        list(decp.iter_json_markets(write_json(tmp_path, {"foo": 1})))
    with pytest.raises(json.JSONDecodeError):
        list(decp.iter_json_markets(write_json(tmp_path, '{"marches": [{"id": "M1", "uid": "U1"', "t.json")))


def test_csv_reader_detects_delimiter_aliases_and_header_override(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("uid;id;acheteur_id;acheteur_nom;titulaire_id;titulaire_typeIdentifiant;titulaire_denominationSociale;objet;montant;"
                 "dateNotification;codeCPV;dureeMois;lieuExecution_code;lieuExecution_nom\n"
                 f"U1;M1;2169;Mairie;{SIRET};SIRET;ALPHA;Travaux;45000,5;2025-01-10;45310000;12;69123;Lyon\n", encoding="utf-8")
    r, = list(decp.iter_csv_rows(p))
    assert (r["uid"], r["holder_id"], r["holder_type"], r["amount"], r["place"]) == ("U1", SIRET, "SIRET", "45000,5", "69123 Lyon")
    q = tmp_path / "o.csv"
    q.write_text(f"uid,SIRET_attributaire\nU2,{SIRET}\n", encoding="utf-8")
    assert list(decp.iter_csv_rows(q, {"holder_id": "SIRET_attributaire"}))[0]["holder_id"] == SIRET
    with pytest.raises(ValueError):
        list(decp.iter_csv_rows(q))                                 # no known identifier column and no override
    alias = tmp_path / "a.csv"
    alias.write_text(f"uid,Titulaire_SIRET\nU3,{SIRET}\n", encoding="utf-8")
    assert list(decp.iter_csv_rows(alias))[0]["holder_id"] == SIRET   # a known alias needs no override (case / punctuation ignored)


def test_prepare_rejects_with_a_reason_and_flags_suspect_amounts():
    base = next(decp.market_rows(market()))
    r, why = decp.prepare(base)
    assert why is None and r["siren"] == SIREN and r["decp_uid"] == "UID1" and r["amount"] == Decimal("45000.5") and not r["amount_suspect"]
    assert r["notification_date"] == date(2025, 1, 10) and r["duration_months"] == 12
    assert decp.prepare({**base, "holder_type": "TVA"}) == (None, "INVALID_TITULAIRE_ID")
    assert decp.prepare({**base, "uid": None, "market_id": None}) == (None, "NO_UID")
    fallback, _ = decp.prepare({**base, "uid": None})
    assert fallback["decp_uid"] == "21690123100011M1"               # buyer id || market id
    for amount in (0, -5, 1e9, "9999999999999999"):               # threshold is >= 1e9: 999 999 999 is NOT flagged (no unverified heuristic)
        flagged, _ = decp.prepare({**base, "amount": amount})
        assert flagged["amount_suspect"], amount
    assert decp.prepare({**base, "amount": "9999999999999999"})[0]["amount"] is None          # too large to store: flagged, not stored
    assert decp.prepare({**base, "amount": 999999999})[0]["amount_suspect"] is False
    assert decp.prepare({**base, "amount": None})[0]["amount_suspect"] is False               # unknown amount is not "suspect"
