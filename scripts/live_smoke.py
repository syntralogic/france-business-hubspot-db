#!/usr/bin/env python
"""Live smoke test for the two external clients (run once on a machine with internet access).

Usage:
    export INSEE_API_KEY=...            # needed only for the INSEE checks
    PYTHONPATH=src python scripts/live_smoke.py [--siren 552100554] [--count 100] [--skip-insee] [--skip-annuaire]

Checks (each prints PASS/FAIL, exit code 1 if any check fails):
  1. INSEE: one legal unit by SIREN           (GET /siren/{siren})
  2. INSEE: pull of N establishments          (GET /siret, N rows, flattened with the production mapper)
  3. Annuaire: 2 known SIRENs + 1 unknown     (get_company + map_dirigeants)
No data is written to the database. Output is a short summary only (no personal data is printed).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from fbdb.enrich.annuaire import AnnuaireClient, map_dirigeants
from fbdb.extract.sirene_api import SireneApiClient, flatten_etablissement

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    t0 = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - smoke test must report, not crash
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:200]}"
    results.append((name, ok, f"{detail} ({time.monotonic() - t0:.1f}s)"))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {results[-1][2]}")


def insee_single(client: SireneApiClient, siren: str):
    data = client._get(f"/siren/{siren}", {})
    unit = data.get("uniteLegale")
    return bool(unit and unit.get("siren") == siren), f"siren={siren} found={bool(unit)}"


def insee_batch(client: SireneApiClient, count: int):
    data = client._get("/siret", {"q": "etatAdministratifEtablissement:A", "nombre": count, "tri": "siret"})
    items = data.get("etablissements", [])
    flat = [flatten_etablissement(i) for i in items]
    with_name = sum(1 for f in flat if f.get("denominationUniteLegale") or f.get("nomUniteLegale"))
    return len(flat) == count, f"rows={len(flat)}/{count} with_name={with_name} next_cursor={'yes' if (data.get('header') or {}).get('curseurSuivant') else 'no'}"


def annuaire(known: list[str]):
    c = AnnuaireClient()
    parts = []
    ok = True
    for s in known:
        r = c.get_company(s)
        ok &= r is not None
        parts.append(f"{s}:{'found' if r else 'MISSING'}/directors={len(map_dirigeants(r)) if r else 0}")
    unknown = c.get_company("000000000")
    ok &= unknown is None
    parts.append(f"unknown:{'None' if unknown is None else 'UNEXPECTED'}")
    return ok, " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--siren", default="552100554")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--skip-insee", action="store_true")
    ap.add_argument("--skip-annuaire", action="store_true")
    a = ap.parse_args()

    if not a.skip_insee:
        if not os.environ.get("INSEE_API_KEY"):
            print("INSEE_API_KEY not set: skipping INSEE checks (use --skip-insee to silence)")
        else:
            client = SireneApiClient()
            check("insee-single-siren", lambda: insee_single(client, a.siren))
            check(f"insee-pull-{a.count}", lambda: insee_batch(client, a.count))
    if not a.skip_annuaire:
        check("annuaire-live", lambda: annuaire([a.siren, "356000000"]))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
