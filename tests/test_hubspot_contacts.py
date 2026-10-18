import csv

from fbdb.export import hubspot_contacts as hc


def rec(**kw):
    base = {"first_name": "Luc", "last_name": "Martin", "job_title": "Gérant", "email": "Luc@Alpha.fr", "phone": None,
            "mobile": "06 98 76 54 32", "company_name": "ALPHA", "company_domain": "alpha.fr", "siren": "552100554",
            "function_category": "director", "decision_maker_confirmed": False, "email_evidence": "published_with_name",
            "phone_evidence": None, "mobile_evidence": "provider_attributed"}
    return {**base, **kw}


def test_row_is_normalised_and_evidence_kept():
    r = hc.build_row(rec())
    assert r["email"] == "luc@alpha.fr" and r["mobile"] == "+33698765432" and r["phone"] == "" and r["decision_maker_confirmed"] == "Non"
    assert r["mobile_evidence"] == "provider_attributed" and r["phone_evidence"] == ""


def test_invalid_value_is_blanked_with_its_evidence():
    r = hc.build_row(rec(email="not-an-email"))
    assert r["email"] == "" and r["email_evidence"] == ""


def test_export_writes_csv_and_skips_people_without_valid_contact(tmp_path):
    out = hc.export_contacts([rec(), rec(last_name="Durand", email="bad", mobile=None), rec(last_name="")], tmp_path)
    assert out["exported"] == 1 and out["skipped"] == 2 and len(out["files"]) == 1
    rows = list(csv.DictReader(open(out["files"][0], encoding="utf-8")))
    assert list(rows[0].keys()) == hc.HEADERS and rows[0]["Mobile Phone Number"] == "+33698765432"


def test_empty_export_writes_no_file(tmp_path):
    assert hc.export_contacts([], tmp_path) == {"exported": 0, "skipped": 0, "files": []}
