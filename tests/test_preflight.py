"""Preflight: what is configured and what will run. Pure checks; the PostgreSQL part is in tests/integration."""
from fbdb.report import preflight as p


def by(res):
    return {r["service"]: r for r in res["services"]}


def test_services_ready_missing_and_never_printing_values():
    env = {"DATABASE_URL": "postgresql://u:SECRETPW@h/db", "INSEE_API_KEY": "key-insee", "PAPPERS_API_KEY": "tok-pappers",
           "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "  "}
    res = p.check(env, lambda t: True)
    s = by(res)
    assert s["INSEE SIRENE"]["ready"] and s["Pappers"]["ready"]
    assert not s["Twilio Lookup"]["ready"] and s["Twilio Lookup"]["missing"] == ["TWILIO_AUTH_TOKEN"]    # blank = not set
    assert not s["Firecrawl"]["ready"] and s["Firecrawl"]["paid"] and not s["INPI RNE"]["paid"]
    assert res["blocking"] == [] and res["missing_tables"] == []
    text = p.render(res)
    for secret in ("SECRETPW", "key-insee", "tok-pappers", "AC1"):
        assert secret not in text                                                                          # values are never printed
    assert "missing: FIRECRAWL_API_KEY" in text and "BAN geocoding" in text


def test_blocking_without_database_or_insee_key_and_missing_tables_are_listed():
    assert p.check({"INSEE_API_KEY": "k"})["blocking"] == ["DATABASE_URL"]
    assert p.check({"DATABASE_URL": "x"})["blocking"] == ["INSEE_API_KEY"]
    res = p.check({"DATABASE_URL": "x", "INSEE_API_KEY": "k"}, lambda t: t != "public_contracts")
    assert res["missing_tables"] == ["public_contracts"] and "MISSING TABLES" in p.render(res)
