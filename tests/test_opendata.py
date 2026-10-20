"""Open-data downloader: resource choice and streaming download, with a fake HTTP transport (no network)."""
import httpx
import pytest

from fbdb.common import opendata as od

RES = [
    {"title": "decp.parquet", "format": "parquet", "type": "main", "url": "https://x/decp.parquet", "last_modified": "2026-10-10"},
    {"title": "decp.csv (old)", "format": "csv", "type": "main", "url": "https://x/old.csv", "last_modified": "2026-09-01"},
    {"title": "decp.csv", "format": "CSV", "type": "main", "url": "https://x/decp.csv", "last_modified": "2026-10-10T03:00:00"},
    {"title": "schema.csv", "format": "csv", "type": "documentation", "url": "https://x/schema.csv", "last_modified": "2026-10-19"},
    {"title": "no url", "format": "csv", "type": "main", "url": None},
]


def test_pick_resource_newest_main_of_the_format_never_documentation_or_other_format():
    assert od.pick_resource(RES, "csv")["url"] == "https://x/decp.csv"
    assert od.pick_resource(RES, "parquet")["url"] == "https://x/decp.parquet"
    assert od.pick_resource(RES, "csv", "old")["url"] == "https://x/old.csv"


def test_pick_resource_error_lists_what_was_found_and_guesses_nothing():
    with pytest.raises(od.OpenDataError) as e:
        od.pick_resource(RES, "json")
    assert "decp.parquet" in str(e.value) and "--format" in str(e.value)
    with pytest.raises(od.OpenDataError):
        od.pick_resource([], "csv")


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_dataset_resource_reads_the_api():
    def h(req):
        assert req.url.path.endswith("/datasets/abc123/")
        return httpx.Response(200, json={"title": "DECP tabulaire", "page": "https://www.data.gouv.fr/datasets/abc123", "resources": RES})
    r = od.dataset_resource("abc123", "csv", client=client(h))
    assert r["url"] == "https://x/decp.csv" and r["dataset_title"] == "DECP tabulaire" and r["dataset_page"].endswith("/abc123")


def test_download_streams_to_file_and_leaves_no_part_file(tmp_path):
    body = b"a;b\n" * 100000
    n = od.download("https://x/f.csv", tmp_path / "d" / "f.csv", client(lambda req: httpx.Response(200, content=body)))
    assert n == len(body) and (tmp_path / "d" / "f.csv").read_bytes() == body
    assert list((tmp_path / "d").glob("*.part")) == []


@pytest.mark.parametrize("resp,err", [
    (httpx.Response(200, content=b"<html>login</html>", headers={"content-type": "text/html; charset=utf-8"}), od.OpenDataError),
    (httpx.Response(200, content=b""), od.OpenDataError),
    (httpx.Response(403, content=b"no"), httpx.HTTPStatusError),
])
def test_download_refuses_html_empty_and_http_errors_and_keeps_no_file(tmp_path, resp, err):
    with pytest.raises(err):
        od.download("https://x/f.csv", tmp_path / "f.csv", client(lambda req: resp))
    assert list(tmp_path.iterdir()) == []
