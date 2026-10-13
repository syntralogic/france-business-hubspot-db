from datetime import date

import pytest

from fbdb.enrich.ademe_rge import parse_date, read_rows

CSV = """siret;nom_entreprise;nom_qualification;domaine;organisme;date_debut;date_fin
82454695600023;ALPHA;QualiPAC;Pompe a chaleur;Qualit'EnR;2024-01-15;2026-01-14
8245469560002;BETA;Qualibat 5111;Isolation;Qualibat;15/02/2023;
12 34;GAMMA;;Solaire;;;
"""


def test_read_rows_aliases_dates_and_bad_sirets(tmp_path):
    p = tmp_path / "rge.csv"
    p.write_text(CSV, encoding="utf-8")
    rows = list(read_rows(p))
    assert rows[0] == {"siret": "82454695600023", "raw_siret": "82454695600023", "label": "QualiPAC (Pompe a chaleur)",
                       "valid_from": date(2024, 1, 15), "valid_to": date(2026, 1, 14)}
    assert rows[1]["siret"] == "" and rows[1]["valid_from"] == date(2023, 2, 15) and rows[1]["valid_to"] is None
    assert rows[2]["siret"] == "" and rows[2]["label"] == "Solaire"


def test_comma_delimiter_and_missing_siret_column(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("SIRET,Nom Qualification\n82454695600023,X\n", encoding="utf-8")
    assert list(read_rows(p))[0]["label"] == "X"
    q = tmp_path / "b.csv"
    q.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        list(read_rows(q))


def test_parse_date():
    assert parse_date("2025-03-01T00:00:00") == date(2025, 3, 1)
    assert parse_date("") is None and parse_date("n/a") is None
