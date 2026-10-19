"""Website proof -> phones, emails, people and person links stored in a REAL PostgreSQL."""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from fbdb.enrich import website as w  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
SIREN = "552100554"

HOME = {"markdown": "Alpha Elec", "links": ["/mentions-legales", "/contact", "https://facebook.com/alphaelec", "mailto:contact@alpha-elec.fr"]}
LEGAL = {"markdown": "SIREN 552 100 554\nDirecteur de la publication : Luc MARTIN", "links": []}
CONTACT = {"markdown": "Standard : 04 72 00 00 00\nLuc MARTIN\nGérant\nDirect : 06 98 76 54 32 luc.martin@alpha-elec.fr\n"
                       "Eva Durand, Responsable commercial\nSAV : 06 11 22 33 44", "links": ["tel:0472000000"]}


class Fake:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def scrape(self, url):
        self.calls.append(url)
        return self.pages.get(url)


@pytest.fixture()
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(p.name for p in SQL_DIR.glob("*.sql")):
            conn.execute((SQL_DIR / f).read_text(encoding="utf-8"))
        cid = conn.execute("INSERT INTO companies (siren, legal_name, status, is_diffusible) VALUES (%s,'ALPHA ELEC','active',TRUE) RETURNING company_id", (SIREN,)).fetchone()[0]
        conn.execute("INSERT INTO company_addresses (company_id, siret, is_head_office, city) VALUES (%s,'55210055400017',TRUE,'LYON')", (cid,))
        conn.execute("INSERT INTO company_directors (company_id, first_name, last_name, role) VALUES (%s,'Luc','MARTIN','Gerant')", (cid,))
        conn.execute("INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, value_status) VALUES (%s,'website','alpha-elec.fr','alpha-elec.fr','sourced')", (cid,))
    yield cid


def test_end_to_end(db):
    client = Fake({"https://alpha-elec.fr": HOME, "https://alpha-elec.fr/mentions-legales": LEGAL, "https://alpha-elec.fr/contact": CONTACT})
    with psycopg.connect(DSN) as conn:
        from fbdb.enrich.interlocutors import sync_directors
        sync_directors(conn)                                    # register director exists first
    stats = w.run(DSN, client=client, limit=10)
    assert stats["proven"] == 1 and stats["phones_added"] == 3 and stats["emails_added"] == 2 and stats["people_added"] == 1
    assert client.calls == ["https://alpha-elec.fr", "https://alpha-elec.fr/mentions-legales", "https://alpha-elec.fr/contact"]
    with psycopg.connect(DSN) as conn:
        rows = conn.execute("SELECT contact_type, value_normalized, contact_use, is_primary, interlocutor_id IS NOT NULL, attribution_level, "
                            "value_status FROM company_contacts WHERE contact_type IN ('phone','email') ORDER BY contact_type, value_normalized").fetchall()
        by = {(r[0], r[1]): r for r in rows}
        assert by[("phone", "+33698765432")][4:] == (True, "published_with_name", "sourced")      # Luc's direct line
        assert by[("email", "luc.martin@alpha-elec.fr")][4] is True
        assert by[("phone", "+33472000000")][4] is False and by[("phone", "+33472000000")][2] == "switchboard"
        assert by[("phone", "+33611223344")][2] == "after_sales" and by[("phone", "+33611223344")][4] is False   # Eva's block holds a SAV line, not hers
        assert by[("email", "contact@alpha-elec.fr")][2] == "general"
        assert sum(1 for r in rows if r[0] == "phone" and r[3]) == 1           # exactly one primary phone
        people = conn.execute("SELECT last_name, legal_role, operational_function, function_category, director_id IS NOT NULL "
                              "FROM company_interlocutors ORDER BY lower(last_name)").fetchall()
        assert people[1] == ("MARTIN", "Gerant", "Directeur de la publication", "director", True)   # the register director was reused, not duplicated
        assert people[0][0] == "Durand" and people[0][3] == "sales" and len(people) == 2
        proof = conn.execute("SELECT match_proof FROM company_contacts WHERE value_normalized = '+33698765432'").fetchone()[0]
        assert "contact" in proof and "SIREN" in proof
        assert conn.execute("SELECT count(*) FROM company_contacts WHERE contact_type = 'social'").fetchone()[0] == 1


def test_unproven_site_stores_nothing(db):
    client = Fake({"https://alpha-elec.fr": {"markdown": "Tel 04 72 00 00 00 contact@x.fr Luc MARTIN Gérant", "links": []}})
    stats = w.run(DSN, client=client, limit=10)
    assert stats["not_proven"] == 1 and stats["phones_added"] == 0
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM company_contacts WHERE contact_type IN ('phone','email')").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM company_interlocutors").fetchone()[0] == 0


def test_save_pages_keeps_the_pages_of_a_proven_site_only(db, tmp_path):
    client = Fake({"https://alpha-elec.fr": HOME, "https://alpha-elec.fr/mentions-legales": LEGAL, "https://alpha-elec.fr/contact": CONTACT})
    w.run(DSN, client=client, limit=10, save_pages_dir=str(tmp_path / "pages"))
    files = sorted(p.name for p in (tmp_path / "pages").iterdir())
    assert files == [f"{SIREN}__01.md", f"{SIREN}__02.md", f"{SIREN}__03.md"]
    first = (tmp_path / "pages" / files[0]).read_text(encoding="utf-8")
    assert first.startswith("<!-- source: https://alpha-elec.fr -->\nAlpha Elec")
    from fbdb.report import people_eval
    rows, _ = people_eval.rows_for(people_eval.read_pages(str(tmp_path / "pages")), [SIREN])
    assert {(r["first_name"], r["last_name"]) for r in rows} == {("Luc", "Martin"), ("Eva", "Durand")}      # re-run offline on the saved pages


def test_save_pages_writes_nothing_for_an_unproven_site(db, tmp_path):
    client = Fake({"https://alpha-elec.fr": {"markdown": "Tel 04 72 00 00 00 Luc MARTIN Gérant", "links": []}})
    w.run(DSN, client=client, limit=10, save_pages_dir=str(tmp_path / "pages"))
    assert not (tmp_path / "pages").exists()
