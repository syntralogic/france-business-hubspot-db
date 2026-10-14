"""Dedup precision/recall evaluation on a hand-labelled sample (acceptance criterion 3).

Two steps, both file based (no database needed):

1. ``sample``: take a CSV of candidate pairs, run ``compare`` on each, write a labelling sheet
   with an empty ``label`` column (stratified so every outcome is represented).
2. ``score``: after a human fills ``label`` with ``same`` or ``different``, compute precision of
   automatic matches, recall, and whether the >= 99% precision gate passes.

Pair CSV columns: ``a_<field>`` and ``b_<field>`` for field in name, siren, siret, street,
postal_code, city, phone, email, website (missing columns are treated as empty).

    python -m fbdb.report.dedup_eval sample pairs.csv --out to_label.csv --n 500
    python -m fbdb.report.dedup_eval score labelled.csv --out reports/dedup_eval.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from typing import Iterable

from fbdb.match.dedup import (
    AUTO_MATCH, DISTINCT, REVIEW, SAME_COMPANY_OTHER_ESTABLISHMENT, Record, compare,
)

FIELDS = ["name", "siren", "siret", "street", "postal_code", "city", "phone", "email", "website"]
OUTCOMES = [AUTO_MATCH, REVIEW, SAME_COMPANY_OTHER_ESTABLISHMENT, DISTINCT]
PRECISION_GATE = 0.99
VALID_LABELS = {"same", "different"}


def _record(row: dict, side: str) -> Record:
    return Record(**{f: (row.get(f"{side}_{f}") or "").strip() or None for f in FIELDS if f != "name"},
                  name=(row.get(f"{side}_name") or "").strip())


def predict(row: dict):
    return compare(_record(row, "a"), _record(row, "b"))


def stratified_sample(rows: list[dict], n: int, seed: int = 42) -> list[dict]:
    """Up to n rows, split evenly across predicted outcomes (small buckets are taken whole)."""
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[predict(r).outcome].append(r)
    for b in buckets.values():
        rng.shuffle(b)
    picked: list[dict] = []
    remaining = n
    order = sorted(buckets, key=lambda k: len(buckets[k]))
    for i, k in enumerate(order):
        share = max(1, remaining // (len(order) - i))
        take = buckets[k][:share]
        picked.extend(take)
        remaining -= len(take)
    rng.shuffle(picked)
    return picked


def score(rows: Iterable[dict]) -> dict:
    """Confusion counts and precision/recall from labelled rows (label in {same, different})."""
    counts: Counter = Counter()
    skipped = 0
    for r in rows:
        label = (r.get("label") or "").strip().lower()
        if label not in VALID_LABELS:
            skipped += 1
            continue
        counts[(predict(r).outcome, label)] += 1

    # A "same_company_other_establishment" pair is a different establishment: it is correct
    # when labelled "different", and is not an automatic match.
    tp = counts[(AUTO_MATCH, "same")]
    fp = counts[(AUTO_MATCH, "different")]
    review_same = counts[(REVIEW, "same")]
    missed_same = sum(counts[(o, "same")] for o in (DISTINCT, SAME_COMPANY_OTHER_ESTABLISHMENT))
    precision = tp / (tp + fp) if (tp + fp) else None
    total_same = tp + review_same + missed_same
    recall_auto = tp / total_same if total_same else None
    recall_auto_review = (tp + review_same) / total_same if total_same else None
    labelled = sum(counts.values())
    return {
        "labelled_pairs": labelled,
        "skipped_unlabelled": skipped,
        "confusion": {f"{o}|{l}": counts[(o, l)] for o in OUTCOMES for l in sorted(VALID_LABELS)},
        "auto_match_precision": precision,
        "recall_auto_only": recall_auto,
        "recall_auto_plus_review": recall_auto_review,
        "meets_gate": bool(precision is not None and precision >= PRECISION_GATE and labelled >= 500),
        "gate": {"min_precision": PRECISION_GATE, "min_labelled_pairs": 500},
    }


def _read(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("pairs")
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=500)
    s.add_argument("--seed", type=int, default=42)
    c = sub.add_parser("score")
    c.add_argument("labelled")
    c.add_argument("--out")
    args = p.parse_args(argv)

    if args.cmd == "sample":
        rows = _read(args.pairs)
        picked = stratified_sample(rows, args.n, args.seed)
        cols = list(rows[0].keys()) if rows else []
        extra = ["predicted_outcome", "predicted_score", "predicted_reasons", "label"]
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols + [e for e in extra if e not in cols])
            w.writeheader()
            for r in picked:
                d = predict(r)
                w.writerow({**r, "predicted_outcome": d.outcome, "predicted_score": f"{d.score:.3f}",
                            "predicted_reasons": ";".join(d.reasons), "label": ""})
        print(f"wrote {len(picked)} pairs to {args.out}; fill 'label' with same|different")
        return 0

    result = score(_read(args.labelled))
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0 if result["meets_gate"] else 1


if __name__ == "__main__":
    sys.exit(main())
