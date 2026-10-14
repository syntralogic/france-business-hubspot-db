#!/usr/bin/env python
"""Offline weekly-delta scale test on SYNTHETIC data (see scripts/make_synthetic_sirene.py).

Prerequisite: the synthetic stock already loaded (sirene_loader) into DATABASE_URL (a throw-away database).
Builds a delta of known size from the stock CSVs, runs the REAL weekly pipeline (fake API client), compares the
pipeline's counters and change history with what was injected, then runs it a second time (idempotence).

Usage: PYTHONPATH=src DATABASE_URL=... python scripts/synthetic_weekly.py --data /tmp/synth [--seed 7]
Exit code 1 if any expectation fails.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import resource
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_synthetic_sirene import fix_siren, fix_siret  # noqa: E402
from fbdb.load import weekly  # noqa: E402

NEW_TS = "2026-10-12T09:00:00.000000"


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def changed_since(self, since):
        return iter(self.rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--addr", type=int, default=8000)
    ap.add_argument("--close", type=int, default=2000)
    ap.add_argument("--new-estab", type=int, default=1000)
    ap.add_argument("--new-comp", type=int, default=500)
    ap.add_argument("--rename", type=int, default=300)
    ap.add_argument("--hq-move", type=int, default=200)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    dsn = os.environ["DATABASE_URL"]

    uls = {r["siren"]: r for r in load_csv(a.data / "StockUniteLegale_utf8.csv")}
    etabs = {r["siret"]: r for r in load_csv(a.data / "StockEtablissement_utf8.csv")}
    with psycopg.connect(dsn) as conn:
        active = conn.execute(
            "SELECT a.siret, c.siren, a.is_head_office, c.is_sole_proprietor FROM company_addresses a "
            "JOIN companies c USING(company_id) WHERE a.status='active' AND c.status='active' ORDER BY a.siret").fetchall()
        n_hist0 = conn.execute("SELECT count(*) FROM company_change_history").fetchone()[0]
    by_siren: dict[str, list] = {}
    for siret, siren, hq, sole in active:
        by_siren.setdefault(siren, []).append((siret, hq, sole))
    rnd.shuffle(active)
    used: set[str] = set()
    rows: list[dict] = []

    def emit(siret_row, ul_override=None):
        r = dict(etabs[siret_row] if isinstance(siret_row, str) else siret_row)
        ul = dict(uls[r["siren"]])
        if ul_override:
            ul.update(ul_override)
        r.update({k: v for k, v in ul.items() if k not in r or r[k] == ""})
        r.update(ul_override or {})
        r["dateDernierTraitementEtablissement"] = NEW_TS
        r["dateDernierTraitementUniteLegale"] = NEW_TS
        rows.append(r)

    exp = {"street": 0, "closed": 0, "new_estab": 0, "new_comp": 0, "renamed": 0, "hq_old_demoted": 0}
    # 1. address changes (non-head establishments are fine, any active one works)
    for siret, siren, hq, sole in active:
        if exp["street"] >= a.addr:
            break
        if siret in used:
            continue
        used.add(siret)
        r = dict(etabs[siret]); r["libelleVoieEtablissement"] = "NOUVELLE ADRESSE " + siret[-4:]
        etabs[siret] = r; emit(siret); exp["street"] += 1
    # 2. closures
    for siret, siren, hq, sole in active:
        if exp["closed"] >= a.close:
            break
        if siret in used:
            continue
        used.add(siret)
        r = dict(etabs[siret]); r["etatAdministratifEtablissement"] = "F"
        etabs[siret] = r; emit(siret); exp["closed"] += 1
    # 3. new establishments of existing companies (non-sole, nic >= 0009)
    cands = [s for s, v in by_siren.items() if not v[0][2]]
    rnd.shuffle(cands)
    for siren in cands[: a.new_estab]:
        base = dict(etabs[by_siren[siren][0][0]])
        siret = fix_siret(siren, "0009")
        base.update(siret=siret, nic=siret[9:], etablissementSiege="false", codePostalEtablissement="69003", libelleCommuneEtablissement="LYON 3EME")
        emit(base); exp["new_estab"] += 1
    # 4. brand-new companies
    for i in range(a.new_comp):
        siren = fix_siren(f"{95_000_000 + i:08d}"); siret = fix_siret(siren, "0001")
        e = {k: "" for k in next(iter(etabs.values()))}
        e.update(siren=siren, siret=siret, nic=siret[9:], statutDiffusionEtablissement="O", etablissementSiege="true",
                 etatAdministratifEtablissement="A", activitePrincipaleEtablissement="43.22A", numeroVoieEtablissement="7",
                 typeVoieEtablissement="RUE", libelleVoieEtablissement="DU NEUF", codePostalEtablissement="31000", libelleCommuneEtablissement="TOULOUSE",
                 dateCreationEtablissement="2026-10-01")
        u = {k: "" for k in next(iter(uls.values()))}
        u.update(siren=siren, statutDiffusionUniteLegale="O", etatAdministratifUniteLegale="A", categorieJuridiqueUniteLegale="5710",
                 denominationUniteLegale=f"NOUVELLE SYNTH {i:05d}", activitePrincipaleUniteLegale="43.22A", dateCreationUniteLegale="2026-10-01")
        e.update(u); rows.append({**e, "dateDernierTraitementEtablissement": NEW_TS, "dateDernierTraitementUniteLegale": NEW_TS}); exp["new_comp"] += 1
    # 5. company renames (all of the company's delta rows carry the same new name)
    renamed = [s for s, v in by_siren.items() if not v[0][2] and s not in {r["siren"] for r in rows}]
    rnd.shuffle(renamed)
    for siren in renamed[: a.rename]:
        emit(by_siren[siren][0][0], {"denominationUniteLegale": uls[siren]["denominationUniteLegale"] + " RENOMMEE"}); exp["renamed"] += 1
    # 6. head-office moves: a NEW siret becomes head office, the old head office is demoted (same delta, new row first)
    movers = [s for s, v in by_siren.items() if len(v) >= 2 and any(x[1] for x in v) and s not in {r["siren"] for r in rows}]
    rnd.shuffle(movers)
    for siren in movers[: a.hq_move]:
        old_hq = next(x for x in by_siren[siren] if x[1])[0]
        base = dict(etabs[old_hq]); siret = fix_siret(siren, "0008")
        base.update(siret=siret, nic=siret[9:], etablissementSiege="true")
        emit(base)
        demoted = dict(etabs[old_hq]); demoted["etablissementSiege"] = "false"; emit(demoted)
        exp["hq_old_demoted"] += 1
    exp["new_estab"] += a.hq_move
    print(f"delta rows: {len(rows)}  injected: {exp}")

    fail = 0
    for n in (1, 2):
        t0 = time.time()
        try:
            stats = weekly.run(None, dsn, client=FakeClient(rows))
        except Exception as exc:  # noqa: BLE001
            print(f"RUN {n}: FAILED {type(exc).__name__}: {str(exc)[:300]}")
            return 1
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        with psycopg.connect(dsn) as conn:
            h = dict(conn.execute("SELECT field_name || ':' || change_type, count(*) FROM company_change_history GROUP BY 1").fetchall())
            total_h = conn.execute("SELECT count(*) FROM company_change_history").fetchone()[0]
        print(f"RUN {n}: {time.time() - t0:.1f}s peak_rss={rss:.0f}MB stats={stats}")
        print(f"        history by field:type = {dict(sorted(h.items()))}  (total {total_h})")
        if n == 1:
            checks = {
                "added_companies": (stats["added_companies"], exp["new_comp"]),
                "added_establishments": (stats["added_establishments"], exp["new_estab"] + exp["new_comp"]),
                "closed": (stats["closed"], exp["closed"]),
                "siret insert history rows == new establishments": (h.get("siret:insert", 0), exp["new_estab"] + exp["new_comp"]),
                "street changes": (h.get("street:update", 0), exp["street"]),
                "company renames": (h.get("legal_name:update", 0), exp["renamed"]),
                "old head offices demoted": (h.get("is_head_office:update", 0), exp["hq_old_demoted"]),
            }
            for name, (got, want) in checks.items():
                ok = got == want
                fail += not ok
                print(f"        {'OK  ' if ok else 'FAIL'} {name}: got {got}, expected {want}")
            h1 = total_h
        else:
            ok = total_h == h1
            fail += not ok
            print(f"        {'OK  ' if ok else 'FAIL'} idempotent: history rows after run 2 = {total_h} (run 1 = {h1})")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
