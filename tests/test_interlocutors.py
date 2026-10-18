import pytest

from fbdb.enrich import interlocutors as il


class Cur:
    def __init__(self, rows=()):
        self.rows, self.calls, self.rowcount = list(rows), [], 1

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


@pytest.mark.parametrize("text,expected", [
    (None, "unknown"), ("  ", "unknown"),
    ("Gérant", "director"), ("Présidente", "director"), ("Co-gérant", "director"),
    ("Directeur commercial", "sales"), ("Responsable commercial", "sales"),
    ("Chargé d'affaires", "project_manager"), ("Conducteur de travaux", "project_manager"),
    ("Responsable administratif", "admin"), ("Comptabilité", "admin"),
    ("Directeur technique", "technical"), ("Technicien SAV", "technical"),
    ("Stagiaire", "other"),
])
def test_classify_function(text, expected):
    assert il.classify_function(text) == expected


def test_link_needs_evidence():
    with pytest.raises(ValueError, match="without proof"):
        il.link_contact(Cur(), 1, 1, "phone", "0612345678", None)
    with pytest.raises(ValueError, match="without proof"):
        il.link_contact(Cur(), 1, 1, "phone", "0612345678", "i_guess")


def test_link_rejects_wrong_type_or_invalid_value():
    with pytest.raises(ValueError, match="not a valid email"):
        il.link_contact(Cur(), 1, 1, "email", "0612345678", "published_with_name")
    with pytest.raises(ValueError, match="not a valid phone"):
        il.link_contact(Cur(), 1, 1, "phone", "abc", "published_with_name")


def test_link_never_creates_a_value():
    cur = Cur([(1,), None])          # person exists, contact does not
    with pytest.raises(ValueError, match="does not hold"):
        il.link_contact(cur, 1, 1, "phone", "0612345678", "published_with_name")
    assert not any("INSERT INTO company_contacts" in c[0] for c in cur.calls)


def test_link_refuses_a_value_already_owned_by_another_person():
    cur = Cur([(1,), (10, 99, "published_with_name")])
    with pytest.raises(ValueError, match="already linked to another person"):
        il.link_contact(cur, 1, 1, "phone", "0612345678", "provider_attributed")


def test_link_does_not_downgrade_evidence():
    cur = Cur([(1,), (10, 1, "confirmed_by_contact")])
    il.link_contact(cur, 1, 1, "phone", "0612345678", "provider_attributed")
    assert cur.calls[-1][1][1] == "confirmed_by_contact"


def test_confirm_requires_a_note():
    with pytest.raises(ValueError, match="note is required"):
        il.confirm_decision_maker(Cur(), 1, "  ")


def test_upsert_requires_last_name_and_known_source():
    with pytest.raises(ValueError, match="last_name"):
        il.upsert_interlocutor(Cur(), 1, " ")
    with pytest.raises(ValueError, match="unknown data source"):
        il.upsert_interlocutor(Cur([None]), 1, "Martin", source_code="nope")
