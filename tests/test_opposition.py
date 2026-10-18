import pytest

from fbdb.common import provenance as p


class Conn:
    def __init__(self, rowcount=1):
        self.calls, self.rowcount = [], rowcount

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def test_email_default_is_the_email_channel_only():
    c = Conn()
    assert p.set_opposition(c, "Jean@Exemple.FR", "asked by email") == 1
    assert [x[1] for x in c.calls] == [("email", "jean@exemple.fr", "email", "asked by email")]


def test_phone_default_blocks_call_and_sms():
    c = Conn()
    assert p.set_opposition(c, "06 12 34 56 78") == 2
    assert [(x[1][0], x[1][1], x[1][2]) for x in c.calls] == [("phone", "+33612345678", "call"), ("phone", "+33612345678", "sms")]


def test_phone_single_channel_and_all_and_dedupe():
    c = Conn()
    p.set_opposition(c, "0612345678", channels=["sms", "sms"])
    assert [x[1][2] for x in c.calls] == ["sms"]
    c = Conn()
    p.set_opposition(c, "0612345678", channels=["all"])
    assert [x[1][2] for x in c.calls] == ["call", "sms"]


def test_channel_not_valid_for_the_value_type_is_refused_before_any_write():
    c = Conn()
    with pytest.raises(ValueError):
        p.set_opposition(c, "jean@exemple.fr", channels=["call"])
    with pytest.raises(ValueError):
        p.set_opposition(c, "0612345678", channels=["email"])
    assert c.calls == []


def test_invalid_value_is_refused():
    with pytest.raises(ValueError):
        p.set_opposition(Conn(), "not a contact")
    with pytest.raises(ValueError):
        p.opposed_channels(Conn(), "not a contact")


def test_already_opposed_counts_zero():
    assert p.set_opposition(Conn(rowcount=0), "jean@exemple.fr") == 0
