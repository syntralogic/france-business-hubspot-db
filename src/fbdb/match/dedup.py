"""Deduplication decisions between two records.

Order of evidence:
1. Same SIRET            -> same establishment (certain)
2. Same SIREN            -> same company; different SIRET = different establishment (NOT a duplicate)
3. No shared identifier  -> fuzzy evidence, needs corroboration. Name alone or address alone never merges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

from fbdb.transform.normalize import (
    address_hash,
    clean_digits,
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone_fr,
)

AUTO_MATCH = "match"
REVIEW = "review"
DISTINCT = "distinct"
SAME_COMPANY_OTHER_ESTABLISHMENT = "same_company_other_establishment"

NAME_STRONG = 92
NAME_WEAK = 85
NAME_REVIEW = 80  # review only: cheap to check by a human, costly to miss


@dataclass
class Record:
    name: str = ""
    siren: Optional[str] = None
    siret: Optional[str] = None
    street: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None


@dataclass
class Decision:
    outcome: str
    score: float
    reasons: list[str] = field(default_factory=list)


def _siren_of(r: Record) -> Optional[str]:
    if r.siren:
        return clean_digits(r.siren) or None
    if r.siret and len(clean_digits(r.siret)) == 14:
        return clean_digits(r.siret)[:9]
    return None


def compare(a: Record, b: Record) -> Decision:
    sa, sb = clean_digits(a.siret), clean_digits(b.siret)
    if sa and sb:
        if sa == sb:
            return Decision(AUTO_MATCH, 1.0, ["same_siret"])
    siren_a, siren_b = _siren_of(a), _siren_of(b)
    if siren_a and siren_b:
        if siren_a == siren_b:
            return Decision(SAME_COMPANY_OTHER_ESTABLISHMENT, 1.0, ["same_siren_different_siret"])
        # Two valid but different SIRENs are different legal entities, whatever the name.
        return Decision(DISTINCT, 0.0, ["different_siren"])

    # No identifier on at least one side: fuzzy evidence
    reasons: list[str] = []
    name_a, name_b = normalize_company_name(a.name), normalize_company_name(b.name)
    name_sim = fuzz.token_sort_ratio(name_a, name_b) if name_a and name_b else 0.0

    phone_eq = bool(normalize_phone_fr(a.phone)) and normalize_phone_fr(a.phone) == normalize_phone_fr(b.phone)
    email_eq = bool(normalize_email(a.email)) and normalize_email(a.email) == normalize_email(b.email)
    dom_a, dom_b = normalize_domain(a.website), normalize_domain(b.website)
    domain_eq = bool(dom_a) and dom_a == dom_b
    addr_eq = (
        bool(a.street and b.street)
        and address_hash(a.street, a.postal_code, a.city) == address_hash(b.street, b.postal_code, b.city)
    )
    same_postal = bool(a.postal_code) and clean_digits(a.postal_code) == clean_digits(b.postal_code)

    if phone_eq:
        reasons.append("same_phone")
    if email_eq:
        reasons.append("same_email")
    if domain_eq:
        reasons.append("same_domain")
    if addr_eq:
        reasons.append("same_address")
    if same_postal:
        reasons.append("same_postal_code")
    reasons.append(f"name_similarity={name_sim:.0f}")

    contact_eq = phone_eq or email_eq or domain_eq
    score = name_sim / 100

    if name_sim >= NAME_WEAK and contact_eq:
        return Decision(AUTO_MATCH, score, reasons)
    if name_sim >= NAME_STRONG and addr_eq:
        return Decision(AUTO_MATCH, score, reasons)
    if name_sim >= NAME_REVIEW and (addr_eq or same_postal):
        return Decision(REVIEW, score, reasons)
    if contact_eq and name_sim >= 60:
        return Decision(REVIEW, score, reasons)
    # Same address but different name (coworking, shared building): distinct. Same name only: distinct.
    return Decision(DISTINCT, score, reasons)
