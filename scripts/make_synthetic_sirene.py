#!/usr/bin/env python
"""Generate SYNTHETIC SIRENE-stock-shaped CSVs (StockUniteLegale / StockEtablissement layout) for offline tests.

NOT real data. Names carry "SYNTH" and every SIREN/SIRET is generated to pass the Luhn check, but a generated number
can coincide with a real company: never import these files into a production or HubSpot account.
Includes the awkward cases of the real register: sole proprietors, several establishments, closed units, non-diffusible
units, old-style NAF codes, accents, missing postal codes, Corsica and overseas departments.

Usage: python scripts/make_synthetic_sirene.py --companies 100000 --out data/synthetic [--seed 1]
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fbdb.extract.sirene_bulk import ETAB_COLUMNS, UL_COLUMNS  # noqa: E402
from fbdb.transform.normalize import is_valid_siren, is_valid_siret  # noqa: E402

NAF = [("10.71C", "BOULANGERIE"), ("43.22A", "PLOMBERIE"), ("43.21A", "ELECTRICITE"), ("43.32A", "MENUISERIE"),
       ("43.31Z", "PLATRERIE"), ("96.02A", "COIFFURE"), ("45.20A", "GARAGE"), ("56.10A", "RESTAURANT"),
       ("69.10Z", "CABINET JURIDIQUE"), ("86.23Z", "CABINET DENTAIRE"), ("47.11B", "EPICERIE"), ("41.20A", "MACONNERIE")]
PLACES = [("69", "LYON 3EME", "69003"), ("31", "TOULOUSE", "31000"), ("75", "PARIS 1ER", "75001"), ("67", "STRASBOURG", "67000"),
          ("13", "MARSEILLE 2EME", "13002"), ("33", "BORDEAUX", "33000"), ("59", "LILLE", "59000"), ("44", "NANTES", "44000"),
          ("20", "AJACCIO", "20000"), ("20", "BASTIA", "20200"), ("97", "POINTE-A-PITRE", "97110"), ("97", "SAINT-DENIS", "97400"),
          ("35", "RENNES", "35000"), ("06", "NICE", "06000"), ("34", "MONTPELLIER", "34000"), ("21", "DIJON", "21000"),
          ("45", "ORLEANS", "45000"), ("57", "METZ", "57000"), ("80", "AMIENS", "80000"), ("63", "CLERMONT-FERRAND", "63000")]
STREETS = [("RUE", "DE LA PAIX"), ("AVENUE", "VICTOR HUGO"), ("BOULEVARD", "GAMBETTA"), ("PLACE", "DE LA REPUBLIQUE"),
           ("RUE", "DES ORFEVRES"), ("CHEMIN", "DES ECOLIERS"), ("IMPASSE", "DU MOULIN"), ("ROUTE", "DE L'EGLISE"),
           ("RUE", "SAINT-ETIENNE"), ("ALLEE", "JEAN JAURES")]
FIRST = ["Marie", "Jean", "Anna", "Paul", "Léa", "Hélène", "François", "Zoé", "Mathieu", "Chloé"]
LAST = ["MARTIN", "BERNARD", "DUBOIS", "MÜLLER", "LEFÈVRE", "GARCIA", "ROUSSEAU", "N'DIAYE", "DUPONT", "LEROY"]
FORMS = ["5499", "5710", "5720", "5498", "6540", "5599"]
BANDS = ["NN", "00", "01", "02", "03", "11", "12", "21"]


def fix_siren(base8: str) -> str:
    return next(base8 + str(d) for d in range(10) if is_valid_siren(base8 + str(d)))


def fix_siret(siren: str, nic4: str) -> str:
    return next(siren + nic4 + str(d) for d in range(10) if is_valid_siret(siren + nic4 + str(d)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", type=int, default=100_000)
    ap.add_argument("--out", type=Path, default=Path("data/synthetic"))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    n_ul = n_et = 0
    with open(a.out / "StockUniteLegale_utf8.csv", "w", newline="", encoding="utf-8") as ful, \
         open(a.out / "StockEtablissement_utf8.csv", "w", newline="", encoding="utf-8") as fet:
        wul = csv.DictWriter(ful, UL_COLUMNS); wul.writeheader()
        wet = csv.DictWriter(fet, ETAB_COLUMNS); wet.writeheader()
        for i in range(a.companies):
            siren = fix_siren(f"{90_000_000 + i:08d}")
            naf, label = rnd.choice(NAF)
            sole = rnd.random() < 0.35
            nondiff = rnd.random() < 0.03
            closed = rnd.random() < 0.08
            old_naf = rnd.random() < 0.01
            created = f"{rnd.randint(1995, 2025)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            ul = {c: "" for c in UL_COLUMNS}
            ul.update(siren=siren, statutDiffusionUniteLegale="N" if nondiff else "O", dateCreationUniteLegale=created,
                      categorieJuridiqueUniteLegale="1000" if sole else rnd.choice(FORMS),
                      activitePrincipaleUniteLegale="151A" if old_naf else naf,
                      etatAdministratifUniteLegale="C" if closed else "A", trancheEffectifsUniteLegale=rnd.choice(BANDS),
                      dateDernierTraitementUniteLegale=f"2026-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}T10:00:00.000000")
            if nondiff:
                ul.update(denominationUniteLegale="[ND]", nomUniteLegale="[ND]", prenomUsuelUniteLegale="[ND]")
            elif sole:
                ul.update(nomUniteLegale=rnd.choice(LAST), prenomUsuelUniteLegale=rnd.choice(FIRST))
            else:
                ul.update(denominationUniteLegale=f"{label} SYNTH {i:06d}", categorieEntreprise=rnd.choice(["PME", "PME", "ETI", ""]))
            wul.writerow(ul); n_ul += 1
            for k in range(1 if sole else rnd.choice([1, 1, 1, 2, 3])):
                dep, city, postal = rnd.choice(PLACES)
                vt, vn = rnd.choice(STREETS)
                e = {c: "" for c in ETAB_COLUMNS}
                e.update(siren=siren, nic=fix_siret(siren, f"{k + 1:04d}")[9:], siret=fix_siret(siren, f"{k + 1:04d}"),
                         statutDiffusionEtablissement="N" if nondiff else "O", dateCreationEtablissement=created,
                         trancheEffectifsEtablissement=rnd.choice(BANDS), etablissementSiege="true" if k == 0 else "false",
                         etatAdministratifEtablissement="F" if (closed or rnd.random() < 0.05) else "A",
                         activitePrincipaleEtablissement="151A" if old_naf else naf,
                         numeroVoieEtablissement=str(rnd.randint(1, 120)), typeVoieEtablissement=vt, libelleVoieEtablissement=vn,
                         codePostalEtablissement="" if rnd.random() < 0.01 else postal, libelleCommuneEtablissement=city,
                         enseigne1Etablissement=f"Chez {rnd.choice(FIRST)} (SYNTH)" if rnd.random() < 0.3 else "",
                         activitePrincipaleRegistreMetiersEtablissement=f"{naf.replace('.', '')}Z" if rnd.random() < 0.4 else "",
                         dateDernierTraitementEtablissement=f"2026-0{rnd.randint(1, 9)}-2{rnd.randint(0, 8)}T08:30:00.000000")
                if nondiff:
                    e.update(numeroVoieEtablissement="", typeVoieEtablissement="", libelleVoieEtablissement="[ND]", libelleCommuneEtablissement="[ND]", codePostalEtablissement="")
                wet.writerow(e); n_et += 1
    print(f"wrote {n_ul} legal units and {n_et} establishments to {a.out}")


if __name__ == "__main__":
    main()
