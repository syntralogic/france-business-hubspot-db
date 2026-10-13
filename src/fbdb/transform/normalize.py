"""Normalization and validation helpers for French business data."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional

import phonenumbers

LEGAL_FORM_NOISE = {
    "SARL", "SAS", "SASU", "EURL", "SA", "SCI", "SNC", "EI", "EIRL", "SELARL", "SCOP",
}
ADDRESS_ABBREVIATIONS = {
    "AV": "AVENUE", "AVE": "AVENUE", "BD": "BOULEVARD", "BLD": "BOULEVARD",
    "CHE": "CHEMIN", "IMP": "IMPASSE", "PL": "PLACE", "RTE": "ROUTE",
    "ST": "SAINT", "STE": "SAINTE", "ZA": "ZONE ARTISANALE", "ZI": "ZONE INDUSTRIELLE",
}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def clean_digits(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


def is_valid_siren(value: Optional[str]) -> bool:
    s = clean_digits(value)
    return len(s) == 9 and _luhn_ok(s)


def is_valid_siret(value: Optional[str]) -> bool:
    s = clean_digits(value)
    if len(s) != 14:
        return False
    if s.startswith("356000000"):  # La Poste: sum of digits divisible by 5
        return sum(int(c) for c in s) % 5 == 0
    return _luhn_ok(s)


def normalize_company_name(name: Optional[str]) -> str:
    """Uppercase, no accents/punctuation, no legal-form suffix. Used for matching only."""
    if not name:
        return ""
    s = strip_accents(name).upper()
    # collapse dotted abbreviations first: S.A.R.L. -> SARL
    s = re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m.group(0).replace(".", ""), s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    tokens = [t for t in s.split() if t not in LEGAL_FORM_NOISE]
    return " ".join(tokens)


def normalize_address(street: Optional[str], postal_code: Optional[str], city: Optional[str]) -> str:
    s = strip_accents(street or "").upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    tokens = [ADDRESS_ABBREVIATIONS.get(t, t) for t in s.split()]
    city_n = re.sub(r"[^A-Z0-9 ]", " ", strip_accents(city or "").upper())
    city_n = re.sub(r"\b\d+(ER|EME|E)\b", "", city_n).strip()  # LYON 3EME -> LYON
    return " ".join(tokens + [clean_digits(postal_code), " ".join(city_n.split())]).strip()


def address_hash(street: Optional[str], postal_code: Optional[str], city: Optional[str]) -> str:
    return hashlib.sha1(normalize_address(street, postal_code, city).encode("utf-8")).hexdigest()


def normalize_phone_fr(value: Optional[str]) -> Optional[str]:
    """Return E.164 or None. Never guesses: invalid numbers return None."""
    if not value:
        return None
    try:
        num = phonenumbers.parse(value, "FR")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def normalize_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    return v if _EMAIL_RE.match(v) else None


def normalize_domain(url: Optional[str]) -> Optional[str]:
    """https://www.Example.fr/path -> example.fr"""
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^[a-z]+://", "", u)
    u = u.split("/")[0].split("?")[0].split(":")[0]
    u = re.sub(r"^www\.", "", u)
    return u if "." in u else None
