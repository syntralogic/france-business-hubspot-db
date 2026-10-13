from fbdb.load.reference import normalize_naf, parse_legal_forms, parse_naf


def test_normalize_naf():
    assert normalize_naf("0111Z") == "01.11Z"
    assert normalize_naf("10.71c") == "10.71C"
    assert normalize_naf(" 43.22a ") == "43.22A"          # surrounding/inner spaces and case are tolerated
    assert normalize_naf("43-22A") is None                # a different separator is not repaired, it is rejected
    assert normalize_naf("43.22A") == "43.22A"
    assert normalize_naf("A") is None and normalize_naf("10.7") is None and normalize_naf("") is None
    assert normalize_naf(None) is None


def test_parse_naf_semicolon_bom_and_levels(tmp_path):
    p = tmp_path / "naf.csv"
    p.write_bytes("﻿Code;Libellé\n".encode("utf-8")
                  + "A;Agriculture, sylviculture et pêche\n01;Culture et production animale\n"
                    "0111Z;Culture de céréales\n10.71C;Boulangerie et boulangerie-pâtisserie\n"
                    "10.71C;Doublon\n43.22A;\n\n".encode("utf-8"))
    res = parse_naf(p)
    assert [r["code"] for r in res.rows] == ["01.11Z", "10.71C"]
    assert res.rows[1]["label"] == "Boulangerie et boulangerie-pâtisserie"     # accents survive
    assert res.skipped == 4                                                      # section, division, duplicate, empty label
    assert any("duplicate" in e for e in res.examples) and any("empty label" in e for e in res.examples)


def test_parse_naf_optional_trade_and_category_columns(tmp_path):
    p = tmp_path / "naf.csv"
    p.write_text("code,label,trade,category\n10.71C,Boulangerie,Boulanger,artisan\n43.22A,Plomberie,,\n", encoding="utf-8")
    rows = {r["code"]: r for r in parse_naf(p).rows}
    assert rows["10.71C"]["trade"] == "Boulanger" and rows["10.71C"]["category"] == "artisan"
    assert rows["43.22A"]["trade"] is None                                       # empty means "not curated", not ""


def test_parse_without_recognised_header_uses_first_two_columns(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n10.71C,Boulangerie\n", encoding="utf-8")
    assert [r["code"] for r in parse_naf(p).rows] == ["10.71C"]


def test_parse_legal_forms_keeps_only_level_three_codes(tmp_path):
    p = tmp_path / "cj.csv"
    p.write_text("Code;Libellé\n1;Personne physique\n10;Entrepreneur individuel\n1000;Entrepreneur individuel\n"
                 "5710;SAS, société par actions simplifiée\n5710;dup\n", encoding="utf-8")
    res = parse_legal_forms(p)
    assert [r["code"] for r in res.rows] == ["1000", "5710"]
    assert res.skipped == 3
