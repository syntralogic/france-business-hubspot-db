"""Load scope (client target 500k-1M): NAF prefixes and departments."""
from fbdb.load.scope import Scope


def test_default_scope_keeps_everything(monkeypatch):
    monkeypatch.delenv("SCOPE_NAF", raising=False)
    monkeypatch.delenv("SCOPE_DEPARTMENTS", raising=False)
    s = Scope.from_env()
    assert not s.active and s.keep_company({"main_naf": None}) and s.keep_establishment({"department_code": None}) and s.describe() == "naf=all; departments=all"


def test_naf_prefixes_with_or_without_dots_and_invalid_naf():
    s = Scope.build(naf="43, 71.1 ,43.22A")
    assert s.naf_prefixes == ("4322A", "43", "711") or set(s.naf_prefixes) == {"43", "711", "4322A"}
    assert s.keep_company({"main_naf": "43.21A"}) and s.keep_company({"main_naf": "71.12B"}) and s.keep_company({"main_naf": "43.22A"})
    assert not s.keep_company({"main_naf": "56.30Z"}) and not s.keep_company({"main_naf": "71.20B"})
    assert not s.keep_company({"main_naf": None}) and not s.keep_company({})        # no NAF: not kept when a NAF scope is set


def test_departments_are_normalised():
    s = Scope.build(departments="1, 69,2a,971")
    assert s.departments == {"01", "69", "2A", "971"}
    assert s.keep_establishment({"department_code": "69"}) and s.keep_establishment({"department_code": "1"}) and s.keep_establishment({"department_code": "2A"})
    assert not s.keep_establishment({"department_code": "13"}) and not s.keep_establishment({"department_code": None})


def test_cli_values_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("SCOPE_NAF", "56")
    monkeypatch.setenv("SCOPE_DEPARTMENTS", "13")
    s = Scope.from_env()
    assert s.naf_prefixes == ("56",) and s.departments == {"13"}
    s = Scope.from_env("43", "69")
    assert s.naf_prefixes == ("43",) and s.departments == {"69"}
    monkeypatch.setenv("SCOPE_NAF", "")
    assert not Scope.from_env().naf_prefixes                                           # an empty variable is no scope
