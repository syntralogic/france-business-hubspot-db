import io
import zipfile

from fbdb.load.demo_load import zip_first_entry_lines


def _zip_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.csv", text)
    return buf.getvalue()


def test_reads_lines_from_a_zip_in_small_chunks():
    text = "siren,nom\n" + "".join(f"{i},société {i}\n" for i in range(500))
    raw = _zip_bytes(text)
    chunks = (raw[i:i + 7] for i in range(0, len(raw), 7))
    assert "".join(zip_first_entry_lines(chunks)) == text


def test_works_on_a_truncated_download():
    text = "".join(f"{i},ligne {i}\n" for i in range(5000))
    raw = _zip_bytes(text)[:900]
    lines = list(zip_first_entry_lines(iter([raw])))
    assert lines and lines[0] == "0,ligne 0\n"
