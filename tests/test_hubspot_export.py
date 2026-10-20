import csv
from datetime import date

from fbdb.export.hubspot import HEADERS, MAX_ROWS_PER_FILE, build_row, export_companies

SIREN = "552100554"
SIRET = "55210055400013"


def good(**kw):
    base = dict(internal_id=1, siret=SIRET, siren=SIREN, legal_name="BOULANGERIE DUPONT", status="active",
                is_diffusible=True, street="12 RUE DE LA PAIX", postal_code="69003", city="LYON", naf="10.71C",
                is_head_office=True, phone="+33 1 42 68 53 00", country="France")
    base.update(kw)
    return base


def run(records, tmp_path, **kw):
    res = export_companies(records, tmp_path, run_date=date(2026, 10, 13), **kw)
    errs = list(csv.DictReader(open(res["error_report"], encoding="utf-8")))
    return res, errs


def rows(res):
    return list(csv.DictReader(open(res["files"][0], encoding="utf-8")))


def test_valid_record_exported_with_headers_and_e164(tmp_path):
    res, errs = run([good()], tmp_path)
    assert res["exported"] == 1 and res["rejected"] == 0 and errs == []
    r = rows(res)[0]
    assert list(r.keys()) == HEADERS and r["Phone Number"] == "+33142685300" and r["SIRET"] == SIRET


def test_rejections(tmp_path):
    bad = [
        good(siret="55210055400014"),               # Luhn fail
        good(is_diffusible=False),                  # non-diffusible
        good(status="closed"),                      # closed excluded by default
        good(legal_name="", establishment_name=""), # no name
        good(postal_code="6900"),                   # bad postal code
    ]
    res, errs = run(bad, tmp_path)
    assert res["exported"] == 0 and res["rejected"] == 5
    codes = {e["code"] for e in errs}
    assert {"INVALID_SIRET", "NON_DIFFUSIBLE", "NOT_ACTIVE", "MISSING_NAME", "INVALID_POSTAL_CODE"} <= codes


def test_closed_allowed_when_requested(tmp_path):
    res, _ = run([good(status="closed")], tmp_path, include_closed=True)
    assert res["exported"] == 1


def test_duplicate_siret_rejected(tmp_path):
    res, errs = run([good(), good(internal_id=2)], tmp_path)
    assert res["exported"] == 1 and any(e["code"] == "DUPLICATE_SIRET" for e in errs)


def test_bad_phone_and_inferred_email_blanked_not_rejected(tmp_path):
    res, errs = run([good(phone="12345", email="boss@x.fr", email_status="inferred")], tmp_path)
    r = rows(res)[0]
    assert res["exported"] == 1 and r["Phone Number"] == "" and r["Email professionnel"] == ""
    assert r["Statut email"] == "unavailable"
    assert {"INVALID_PHONE", "INFERRED_EMAIL"} <= {e["code"] for e in errs}


def test_shared_domain_kept_only_on_first_row(tmp_path):
    a = good(website="https://www.exemple.fr")
    b = good(internal_id=2, siret="55210055400021", website="https://exemple.fr/contact")
    res, errs = run([a, b], tmp_path)
    r = rows(res)
    assert r[0]["Company domain name"] == "exemple.fr" and r[1]["Company domain name"] == ""
    assert r[1]["Website URL"] != "" and any(e["code"] == "DOMAIN_SHARED" for e in errs)


def test_shared_domain_kept_when_siret_is_import_key(tmp_path):
    a = good(website="https://www.exemple.fr")
    b = good(internal_id=2, siret="55210055400021", website="https://exemple.fr/contact")
    res = export_companies([a, b], tmp_path, run_date=date(2026, 10, 13), siret_is_import_key=True)
    with open(res["files"][0], newline="", encoding="utf-8") as fh:
        r = list(csv.DictReader(fh))
    assert r[0]["Company domain name"] == "exemple.fr" and r[1]["Company domain name"] == "exemple.fr"


def test_french_characters_roundtrip_utf8(tmp_path):
    res, _ = run([good(legal_name="Café de l'Étoile Müller", city="SAINT-ÉTIENNE")], tmp_path)
    raw = open(res["files"][0], "rb").read()
    assert raw[:3] != b"\xef\xbb\xbf"                      # no BOM
    assert "Café de l'Étoile Müller" in raw.decode("utf-8") and "SAINT-ÉTIENNE" in raw.decode("utf-8")


def test_control_chars_stripped_and_limit_constant():
    assert build_row(good(legal_name="A\x00B\x07C"))["name"] == "ABC"
    assert MAX_ROWS_PER_FILE < 1_048_576


def test_google_ratings_blanked_by_default(tmp_path):
    src = good(google_rating="4.6", google_reviews="120")
    res, _ = run([src], tmp_path)
    r = rows(res)[0]
    assert r["Note Google"] == "" and r["Nombre avis Google"] == ""
    res2 = export_companies([src], tmp_path / "ok", run_date=date(2026, 10, 13), allow_google_ratings=True)
    with open(res2["files"][0], newline="", encoding="utf-8") as fh:
        r2 = list(csv.DictReader(fh))[0]
    assert r2["Note Google"] == "4.6" and r2["Nombre avis Google"] == "120"


def test_allowed_google_rating_is_blanked_when_older_than_the_max_age_and_kept_when_fresh_or_age_unknown(tmp_path):
    from datetime import datetime, timezone
    run_date = date(2026, 10, 13)

    def exported(extra, **kw):
        src = good(google_rating="4.6", google_reviews="120", **extra)
        res = export_companies([src], tmp_path / f"o{len(list(tmp_path.iterdir()))}", run_date=run_date, allow_google_ratings=True, **kw)
        with open(res["files"][0], newline="", encoding="utf-8") as fh:
            row = list(csv.DictReader(fh))[0]
        return row["Note Google"], row["Nombre avis Google"], res

    fresh = {"google_rating_collected_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}      # 12 days
    old = {"google_rating_collected_at": datetime(2026, 8, 1, tzinfo=timezone.utc)}         # 73 days
    assert exported(fresh)[:2] == ("4.6", "120")
    assert exported({})[:2] == ("4.6", "120")                                               # age unknown (not from the view): unchanged
    note, reviews, res = exported(old)
    assert (note, reviews) == ("", "")
    assert exported(old, max_rating_age_days=90)[:2] == ("4.6", "120")                      # the limit is a parameter
    assert res["warnings"] == 1 and res["exported"] == 1                                    # the row is exported, the rating is reported
    assert "GOOGLE_RATING_STALE" in open(res["error_report"], encoding="utf-8").read()
