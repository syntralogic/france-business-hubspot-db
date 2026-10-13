"""Field-level conflict resolution between sources.

Two policies:
- "registry" (identity, status, address, NAF, legal form): official source priority wins, then recency, then confidence.
- "contact"  (phone, email, website): verification status wins, then recency, then source priority, then confidence.
Inferred values are only used when nothing else exists, and are flagged so the exporter can refuse them.
A tie on the winning key between different values is reported as a conflict for human review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

STATUS_RANK = {"verified": 0, "sourced": 1, "inferred": 2}


@dataclass(frozen=True)
class Candidate:
    value: Any
    source_priority: int          # lower = more trusted (data_sources.priority)
    observed_at: datetime
    confidence: float = 0.5
    status: str = "sourced"       # verified | sourced | inferred


@dataclass
class Resolution:
    value: Any
    winner: Optional[Candidate]
    conflict: bool                # True -> open a data_quality_issue
    used_inferred: bool
    alternatives: list[Candidate]


def _key(c: Candidate, policy: str):
    ts = -c.observed_at.timestamp()
    if policy == "registry":
        return (c.source_priority, ts, -c.confidence)
    if policy == "contact":
        return (STATUS_RANK.get(c.status, 3), ts, c.source_priority, -c.confidence)
    raise ValueError(f"unknown policy {policy}")


def resolve(candidates: list[Candidate], policy: str) -> Resolution:
    pool = [c for c in candidates if c.value not in (None, "")]
    if not pool:
        return Resolution(None, None, False, False, [])
    real = [c for c in pool if c.status != "inferred"]
    used_inferred = not real
    ranked = sorted(real or pool, key=lambda c: _key(c, policy))
    best = ranked[0]
    ties = [c for c in ranked[1:] if _key(c, policy) == _key(best, policy) and c.value != best.value]
    return Resolution(best.value, best, bool(ties), used_inferred, [c for c in ranked[1:] if c.value != best.value])
