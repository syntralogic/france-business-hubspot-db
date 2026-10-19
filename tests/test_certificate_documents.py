import pytest

from fbdb.enrich import certificate_documents as d
from fbdb.enrich import certifications as c

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 20


def test_type_is_read_from_the_first_bytes_not_from_the_extension(tmp_path):
    for name, data, mime in (("a.pdf", PDF, "application/pdf"), ("b.txt", PNG, "image/png"), ("c.bin", JPG, "image/jpeg")):
        f = tmp_path / name
        f.write_bytes(data)
        got = d.inspect_file(f)
        assert got["mime"] == mime and got["size"] == len(data) and len(got["sha256"]) == 64 and got["name"] == name


def test_refused_files(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"MZ\x90\x00 an executable named .pdf")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-" + b"0" * d.MAX_BYTES)
    for f, word in ((fake, "not a PDF"), (empty, "empty"), (big, "limit"), (tmp_path / "missing.pdf", "not found")):
        with pytest.raises(d.DocumentError, match=word):
            d.inspect_file(f)


def test_default_kind_and_attestation_is_for_insurance_only():
    assert d.default_kind("RC pro") == "attestation" and d.default_kind("Decennale") == "attestation"
    assert d.default_kind("Qualibat") == "certificate"


def test_certificate_url_keeps_only_http_links_and_never_repairs():
    assert c.clean_url("https://www.qualibat.com/e/123") == "https://www.qualibat.com/e/123"
    assert c.clean_url(" http://x.fr/a ") == "http://x.fr/a"
    for bad in ("", "www.qualibat.com/e/123", "javascript:alert(1)", "ftp://x.fr/a", "https://x.fr/a b", "https://", "https://x.fr/" + "a" * 2000):
        assert c.clean_url(bad) is None


def test_parse_row_carries_the_link_and_flags_an_unusable_one():
    m = {**c.DEFAULT_MAPPING}
    _, _, ok, _ = c.parse_row({"siren": "552100554", "cert_type": "Qualibat", "certificate_url": "https://x.fr/c/1"}, m)
    assert ok["certificate_url"] == "https://x.fr/c/1" and ok["url_invalid"] is None
    _, _, bad, err = c.parse_row({"siren": "552100554", "cert_type": "Qualibat", "certificate_url": "x.fr/c/1"}, m)
    assert err is None and bad["certificate_url"] is None and bad["url_invalid"] == "x.fr/c/1"   # the certification still loads
