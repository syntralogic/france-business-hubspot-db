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
    source_id: Optional[int] = None   # data_sources.source_id, needed to persist conflicts (provenance)


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


def persist_conflicts(conn, company_id: int, field_name: str, res: Resolution, establishment_id=None) -> int:
    """Keeps every losing value of a resolution in field_conflicts instead of silently dropping it.

    Returns the number of conflicting values passed to record_conflict (duplicates are ignored by the DB).
    Does nothing when there is no winner or no differing alternative.
    """
    if res.winner is None or not res.alternatives:
        return 0
    from fbdb.common.provenance import record_conflict  # lazy: keeps resolve() free of DB imports

    n = 0
    for alt in res.alternatives:
        if str(alt.value) == str(res.value):
            continue
        record_conflict(conn, company_id, field_name, res.value, res.winner.source_id,
                        alt.value, alt.source_id, establishment_id)
        n += 1
    return n


def contact_conflict(cur, company_id: int, ctype: str, new_value: str, new_source_id, new_status: str = "sourced",
                     establishment_id=None, now: Optional[datetime] = None) -> int:
    """Keep a phone/email that disagrees with the primary one already held (another source) in field_conflicts.

    Call it BEFORE inserting the new contact. Nothing is dropped or re-ranked: the new value is still stored as a contact and
    the existing primary stays the exported one (`kept_value`); the row only tells a human that two sources disagree.
    Not a conflict: same value, same source (a site listing two numbers), or no primary yet. Returns the rows recorded.
    """
    if ctype not in ("phone", "email"):
        return 0
    cur.execute(
        """SELECT k.value_normalized, k.value_status, k.source_id, k.collected_at, coalesce(d.priority, 99)
             FROM company_contacts k LEFT JOIN data_sources d ON d.source_id = k.source_id
            WHERE k.company_id = %s AND k.contact_type = %s AND k.is_primary AND k.establishment_id IS NOT DISTINCT FROM %s
              AND k.value_normalized <> %s AND k.source_id IS DISTINCT FROM %s LIMIT 1""",
        (company_id, ctype, establishment_id, new_value, new_source_id))
    row = cur.fetchone()
    if row is None:
        return 0
    when = now or datetime.now().astimezone()
    kept = Candidate(row[0], row[4], row[3], 0.5, row[1], row[2])
    other = Candidate(new_value, 99, when, 0.5, new_status, new_source_id)
    return persist_conflicts(cur, company_id, ctype, Resolution(kept.value, kept, True, False, [other]), establishment_id)
