"""What is configured and what will run: `python -m fbdb.report.preflight` (no network call, no key value is ever printed).

Run it before the first live test and after every change of environment variables. It says, for every service: whether its
variables are set, which command it feeds, whether the weekly run starts it by itself, and which tables of the database are missing
(a migration that was not applied). Exit code 1 when the database or the INSEE key (without which nothing is loaded) is missing,
or with --strict when any paid key is missing.
"""
from __future__ import annotations

import argparse
import os
from typing import Callable, Mapping, Optional

# (service, [env vars that must ALL be set], command, how the weekly run uses it, paid?)
SERVICES = [
    ("INSEE SIRENE", ["INSEE_API_KEY"], "scripts/run_weekly.sh (load + weekly delta)", "automatic", False),
    ("INPI RNE", ["INPI_USERNAME", "INPI_PASSWORD"], "python -m fbdb.enrich.inpi --limit 5", "automatic when set", False),
    ("Pappers", ["PAPPERS_API_KEY"], "python -m fbdb.enrich.pappers --limit 5", "automatic when set", True),
    ("Google Places", ["GOOGLE_PLACES_API_KEY"], "python -m fbdb.enrich.places --limit 5", "automatic when set", True),
    ("Firecrawl", ["FIRECRAWL_API_KEY"],
     "python -m fbdb.enrich.website|social_search|site_activity --limit 5", "manual (--limit 5 first)", True),
    ("Hunter", ["HUNTER_API_KEY"], "python -m fbdb.enrich.email_verify|email_find --limit 5", "manual", True),
    ("Lusha (direct mobiles)", ["LUSHA_API_KEY", "LUSHA_CONTRACT_REF"], "python -m fbdb.enrich.mobile_find --limit 5", "manual", True),
    ("Twilio Lookup", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"], "python -m fbdb.enrich.phone_lookup --limit 5", "manual", True),
    ("ADEME RGE list", ["RGE_CSV_URL"], "python -m fbdb.enrich.ademe_rge --download", "automatic when set", False),
    ("Licensed contacts", ["LICENSED_CONTACTS_CONTRACT_REF"], "python -m fbdb.enrich.licensed_contacts FILE --contract-ref ...", "manual (contract)", True),
    ("Certification files", ["CERTIFICATIONS_CONTRACT_REF"], "python -m fbdb.enrich.certifications FILE ...", "manual (licence)", True),
    ("Web service", ["RUN_TOKEN"], "fbdb.web (POST /run)", "needed to start a run remotely", False),
]
NO_KEY = ["Annuaire des Entreprises", "BAN geocoding", "BODACC", "DECP (downloaded by itself, DECP_AUTO=1)"]
# one table per migration family that has been added since the first release: a missing one = a migration not applied
EXPECTED_TABLES = ["field_conflicts", "company_interlocutors", "social_searches", "company_activities", "company_site_signals",
                   "public_contracts", "decp_loads", "company_certifications", "certification_loads", "company_site_declarations",
                   "certification_documents", "api_usage"]


def check(env: Mapping[str, str], table_exists: Optional[Callable[[str], bool]] = None) -> dict:
    rows = []
    for name, variables, command, weekly, paid in SERVICES:
        missing = [v for v in variables if not (env.get(v) or "").strip()]
        rows.append({"service": name, "ready": not missing, "missing": missing, "command": command, "weekly": weekly, "paid": paid})
    tables = [t for t in EXPECTED_TABLES if table_exists is not None and not table_exists(t)]
    return {"services": rows, "database_url": bool((env.get("DATABASE_URL") or "").strip()), "missing_tables": tables,
            "blocking": [] if (env.get("DATABASE_URL") and env.get("INSEE_API_KEY")) else
                        [v for v in ("DATABASE_URL", "INSEE_API_KEY") if not (env.get(v) or "").strip()]}


def render(result: dict) -> str:
    out = ["SERVICE               READY  WEEKLY RUN                     COMMAND / MISSING VARIABLES"]
    for r in result["services"]:
        tail = r["command"] if r["ready"] else "missing: " + ", ".join(r["missing"])
        out.append(f"{r['service']:<21} {'yes' if r['ready'] else 'NO ':<6} {r['weekly']:<30} {tail}")
    out.append("No key needed: " + ", ".join(NO_KEY))
    if result["missing_tables"]:
        out.append("MISSING TABLES (migration not applied?): " + ", ".join(result["missing_tables"]))
    if result["blocking"]:
        out.append("BLOCKING: " + ", ".join(result["blocking"]) + " is not set")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 when any paid service is not configured")
    a = ap.parse_args()
    env = os.environ
    exists = None
    if env.get("DATABASE_URL"):
        import psycopg
        try:
            with psycopg.connect(env["DATABASE_URL"]) as conn:
                found = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
            exists = lambda t: t in found  # noqa: E731
        except Exception as exc:                          # never print the URL: it holds the password
            print(f"database unreachable: {type(exc).__name__}")
    res = check(env, exists)
    print(render(res))
    bad = bool(res["blocking"]) or (a.strict and any(r["paid"] and not r["ready"] for r in res["services"]))
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
