from datetime import datetime, timedelta

from fbdb.match.resolve import Candidate, resolve

NOW = datetime(2026, 10, 13)


def test_registry_prefers_official_over_newer_commercial():
    official = Candidate("12 RUE A", 1, NOW - timedelta(days=60))
    commercial = Candidate("14 RUE B", 3, NOW)
    r = resolve([commercial, official], "registry")
    assert r.value == "12 RUE A" and not r.conflict and r.alternatives[0].value == "14 RUE B"


def test_registry_same_priority_uses_recency():
    old = Candidate("OLD", 1, NOW - timedelta(days=30))
    new = Candidate("NEW", 1, NOW)
    assert resolve([old, new], "registry").value == "NEW"


def test_contact_verified_beats_newer_sourced():
    verified = Candidate("+33142685300", 4, NOW - timedelta(days=90), status="verified")
    sourced = Candidate("+33142685399", 4, NOW, status="sourced")
    assert resolve([sourced, verified], "contact").value == "+33142685300"


def test_inferred_only_when_nothing_else_and_flagged():
    inf = Candidate("a@b.fr", 4, NOW, status="inferred")
    real = Candidate("c@d.fr", 4, NOW - timedelta(days=400), status="sourced")
    assert resolve([inf, real], "contact").value == "c@d.fr"
    only = resolve([inf], "contact")
    assert only.value == "a@b.fr" and only.used_inferred is True


def test_exact_tie_between_different_values_is_conflict():
    a = Candidate("X", 2, NOW, 0.8)
    b = Candidate("Y", 2, NOW, 0.8)
    assert resolve([a, b], "registry").conflict is True


def test_empty_and_unknown_policy():
    assert resolve([Candidate("", 1, NOW)], "registry").value is None
    import pytest
    with pytest.raises(ValueError):
        resolve([Candidate("x", 1, NOW)], "nope")


# --- persist_conflicts: losing values are kept, never silently dropped ---
class _FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(params)


def test_persist_conflicts_keeps_losing_values():
    from datetime import datetime, timezone
    from fbdb.match.resolve import Candidate, persist_conflicts, resolve

    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    res = resolve([
        Candidate("0102030405", 1, now, status="verified", source_id=1),
        Candidate("0607080910", 4, now, status="sourced", source_id=4),
    ], "contact")
    conn = _FakeConn()
    assert persist_conflicts(conn, 42, "phone", res) == 1
    params = conn.calls[0]
    # (company_id, establishment_id, field_name, kept_value, kept_source, other_value, other_source)
    assert params == (42, None, "phone", "0102030405", 1, "0607080910", 4)


def test_persist_conflicts_noop_without_alternatives():
    from datetime import datetime, timezone
    from fbdb.match.resolve import Candidate, persist_conflicts, resolve

    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    res = resolve([Candidate("a", 1, now, source_id=1), Candidate("a", 2, now, source_id=2)], "registry")
    conn = _FakeConn()
    assert persist_conflicts(conn, 1, "name", res) == 0
    assert conn.calls == []
