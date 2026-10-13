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
