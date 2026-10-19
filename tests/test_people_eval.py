"""People precision tool (spec section 5): pages -> labelling sheet -> precision. File based, no database, no network."""
import csv
import json

from fbdb.enrich import website
from fbdb.report import people_eval as pe

PAGE_A = "Notre équipe\nJean Dupont - Gérant\n06 12 34 56 78\nChauffage Climatisation\nMarie Martin, responsable commercial\n"
PAGE_B = "Hébergeur : OVH, directeur Octave Klaba\nBienvenue.\n"


def write_pages(tmp_path):
    d = tmp_path / "pages"
    website.save_pages(str(d), "552100554", [("https://alpha.fr/equipe", PAGE_A), ("https://alpha.fr/mentions", PAGE_B)])
    website.save_pages(str(d), "401234562", [("https://beta.fr", "Bienvenue chez Beta.")])
    return str(d)


def test_save_pages_writes_source_line_and_markdown(tmp_path):
    d = write_pages(tmp_path)
    pages = pe.read_pages(d)
    assert sorted(pages) == ["401234562", "552100554"]
    assert pages["552100554"][0] == ("552100554__01.md", "https://alpha.fr/equipe", PAGE_A)
    assert pages["552100554"][1][1] == "https://alpha.fr/mentions"


def test_sample_lists_every_person_with_rule_line_and_attached_contact(tmp_path):
    d = write_pages(tmp_path)
    pages = pe.read_pages(d)
    rows, per_site = pe.rows_for(pages, pe.sample_sites(pages, 50))
    assert per_site == {"401234562": 0, "552100554": 2}
    by = {(r["first_name"], r["last_name"]): r for r in rows}
    assert set(by) == {("Jean", "Dupont"), ("Marie", "Martin")}                # the heading and the hosting credit are not returned
    jean = by[("Jean", "Dupont")]
    assert jean["function"] == "Gérant" and jean["predicted_category"] == "director" and jean["rule"] == "name_and_function"
    assert jean["phones"] == "+33612345678" and jean["page_url"] == "https://alpha.fr/equipe" and jean["person"] == ""


def test_sample_is_reproducible_and_limited(tmp_path):
    pages = {f"55210055{i}": [("f", "u", "")] for i in range(9)}
    assert pe.sample_sites(pages, 4, seed=1) == pe.sample_sites(pages, 4, seed=1) and len(pe.sample_sites(pages, 4)) == 4


def test_score_precision_by_rule_and_the_follow_up_columns():
    base = {"siren": "1", "extra": ""}
    rows = [
        {**base, "rule": "legal_notice", "person": "yes", "function_ok": "yes", "category_ok": "yes", "contacts_ok": "na"},
        {**base, "rule": "name_and_function", "person": "yes", "function_ok": "no", "category_ok": "no", "contacts_ok": "yes"},
        {**base, "rule": "name_and_function", "person": "no", "function_ok": "na", "category_ok": "na", "contacts_ok": "na"},
        {**base, "rule": "name_and_function", "person": "no"},
        {**base, "rule": "name_and_function", "person": ""},                         # not labelled yet: skipped, not counted as right or wrong
        {**base, "rule": "", "person": "yes", "extra": "missed"},                     # a person the extractor missed: recall only
    ]
    r = pe.score(rows)
    assert r["labelled"] == 4 and r["unlabelled_skipped"] == 1 and r["person_precision"] == 0.5
    assert r["person_precision_by_rule"]["legal_notice"]["precision"] == 1.0
    assert r["person_precision_by_rule"]["name_and_function"] == {"yes": 1, "no": 2, "precision": 1 / 3}
    assert r["function_precision"] == {"yes": 1, "no": 1, "precision": 0.5} and r["category_precision"]["precision"] == 0.5
    assert r["contacts_precision"] == {"yes": 1, "no": 0, "precision": 1.0}         # "na" is not an answer
    assert r["missed_people_added_by_hand"] == 1 and r["recall_indicative"] == 2 / 3
    assert r["meets_sample_size"] is False and "threshold" in r["note"]


def test_score_with_nothing_labelled_has_no_precision_instead_of_zero():
    r = pe.score([{"siren": "1", "rule": "legal_notice", "person": ""}])
    assert r["person_precision"] is None and r["recall_indicative"] is None


def test_cli_sample_then_score(tmp_path, capsys):
    d = write_pages(tmp_path)
    out = tmp_path / "to_label.csv"
    assert pe.main(["sample", d, "--out", str(out), "--sites", "10"]) == 0
    assert "1 site(s) with no person found" in capsys.readouterr().out
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert len(rows) == 2 and list(rows[0].keys()) == pe.COLUMNS
    for r in rows:
        r["person"] = "yes"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=pe.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    res = tmp_path / "res" / "people.json"
    assert pe.main(["score", str(out), "--out", str(res)]) == 0
    assert json.loads(res.read_text(encoding="utf-8"))["person_precision"] == 1.0
