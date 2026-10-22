"""Target scope of the SIRENE load: which sectors (NAF) and departments are loaded (client spec: 500k to 1M target records).

The full SIRENE stock holds every active company of France (millions). To load only the target, give NAF prefixes and / or departments:

    python -m fbdb.load.sirene_loader --ul ... --etab ... --only-active --naf 41,42,43,71.1 --departments 69,42,01
    or set SCOPE_NAF="41,42,43,71.1" and SCOPE_DEPARTMENTS="69,42,01" (read by the loader, scripts/initial_load.sh and the weekly delta)

NAF: prefixes of the company's MAIN activity, dots optional ("43", "43.2", "43.22A", "4322A" all work). A company with no valid NAF
is not kept when a NAF scope is set. Departments: metropolitan numbers ("69", "01", "1" -> "01"), "2A" / "2B", overseas "971".."976"; an
establishment is kept only when its department is in the list, and a company with no kept establishment is not loaded.
No scope set = everything (the previous behaviour). Nothing is invented: this only chooses which rows of the official files are kept.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


def _split(value) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [i.strip() for i in items if i and i.strip()]


def _dept(code: str) -> str:
    c = code.strip().upper()
    return c.zfill(2) if c.isdigit() and len(c) < 3 else c


@dataclass(frozen=True)
class Scope:
    naf_prefixes: tuple[str, ...] = ()
    departments: frozenset[str] = frozenset()

    @classmethod
    def build(cls, naf=None, departments=None) -> "Scope":
        prefixes = tuple(sorted({re.sub(r"[^0-9A-Za-z]", "", n).upper() for n in _split(naf)} - {""}))
        return cls(prefixes, frozenset(_dept(d) for d in _split(departments)))

    @classmethod
    def from_env(cls, naf=None, departments=None) -> "Scope":
        """CLI values win over SCOPE_NAF / SCOPE_DEPARTMENTS."""
        return cls.build(naf or os.environ.get("SCOPE_NAF"), departments or os.environ.get("SCOPE_DEPARTMENTS"))

    @property
    def active(self) -> bool:
        return bool(self.naf_prefixes or self.departments)

    def describe(self) -> str:
        return ("naf=" + ",".join(self.naf_prefixes) if self.naf_prefixes else "naf=all") + "; " + \
               ("departments=" + ",".join(sorted(self.departments)) if self.departments else "departments=all")

    def keep_company(self, mapped: dict) -> bool:
        if not self.naf_prefixes:
            return True
        naf = re.sub(r"[^0-9A-Za-z]", "", mapped.get("main_naf") or "").upper()
        return bool(naf) and naf.startswith(self.naf_prefixes)

    def keep_establishment(self, mapped: dict) -> bool:
        return not self.departments or _dept(mapped.get("department_code") or "") in self.departments
