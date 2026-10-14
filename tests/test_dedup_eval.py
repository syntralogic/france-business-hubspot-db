import csv

from fbdb.report.dedup_eval import main, predict, score, stratified_sample


def _pair(an, bn, **kw):
    row = {"a_name": an, "b_name": bn}
    row.update(kw)
    return row


def test_predict_uses_compare():
    r = _pair("Boulangerie Dupont SARL", "BOULANGERIE DUPONT", a_phone="+33142685300", b_phone="01 42 68 53 00")
    assert predict(r).outcome == "match"


def test_score_precision_and_recall():
    match = dict(_pair("Boulangerie Dupont SARL", "BOULANGERIE DUPONT",
                       a_phone="0142685300", b_phone="01 42 68 53 00"))
    rows = [
        {**match, "label": "same"},
        {**match, "label": "same"},
        {**match, "label": "different"},
        {**_pair("Plomberie Martin", "Cabinet Leroy"), "label": "different"},
        {**_pair("Plomberie Martin", "Plomberie Martin"), "label": ""},
    ]
    res = score(rows)
    assert res["labelled_pairs"] == 4 and res["skipped_unlabelled"] == 1
    assert abs(res["auto_match_precision"] - 2 / 3) < 1e-9
    assert res["meets_gate"] is False  # precision < 99% and fewer than 500 pairs


def test_same_establishment_pair_is_not_a_false_positive():
    row = {"a_siret": "55210055400013", "b_siret": "55210055400021", "label": "different"}
    res = score([row])
    assert res["confusion"]["same_company_other_establishment|different"] == 1
    assert res["auto_match_precision"] is None


def test_stratified_sample_keeps_small_buckets():
    rows = [_pair(f"Societe {i}", f"Autre {i}") for i in range(50)]
    rows.append(_pair("Boulangerie Dupont SARL", "BOULANGERIE DUPONT", a_phone="0142685300", b_phone="0142685300"))
    picked = stratified_sample(rows, 10)
    assert len(picked) <= 10
    assert any(predict(r).outcome == "match" for r in picked)


def test_cli_sample_then_score(tmp_path):
    pairs = tmp_path / "pairs.csv"
    with open(pairs, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["a_name", "b_name", "a_phone", "b_phone"])
        w.writeheader()
        w.writerow({"a_name": "Boulangerie Dupont SARL", "b_name": "BOULANGERIE DUPONT",
                    "a_phone": "0142685300", "b_phone": "0142685300"})
        w.writerow({"a_name": "Plomberie Martin", "b_name": "Cabinet Leroy", "a_phone": "", "b_phone": ""})
    out = tmp_path / "to_label.csv"
    assert main(["sample", str(pairs), "--out", str(out), "--n", "10"]) == 0
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert rows and "label" in rows[0] and "predicted_outcome" in rows[0]
    for r in rows:
        r["label"] = "same" if r["predicted_outcome"] == "match" else "different"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    assert main(["score", str(out)]) == 1  # gate needs >= 500 labelled pairs
