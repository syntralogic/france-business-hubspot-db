"""What a company DECLARES about its service area and its team, from the text of its own site (client spec sections 2 and 4). Pure.

Three kinds of declaration, each stored as written with the passage that states it (never resolved, never guessed):
  service_area_place     a town, department or region named as a place the company works (`value` = the name as written)
  service_area_radius_km a radius ("dans un rayon de 30 km autour de Lyon": `number` = 30, `value` = the words of the page)
  service_area_scope     `national` ("partout en France", "toute la France") or `regional` ("toute la region"): mobility only when declared
  team_size              the team size the SITE announces ("une equipe de 12 personnes"): kept apart from the official headcount,
                         `qualifier` = exact / plus_de / environ / pres_de

The address of the company is NOT a service area and a service area is not an address: they are different tables.
Not read: images (alt text), recruitment sentences, a negation ("nous n'intervenons pas a Paris", "hors de ..."), partner or
sub-contractor sentences for the team size, and "vos salaries" / "vos collaborateurs" (the client's staff, not the company's).
Known limit: a list written as bullet lines under a "Zone d'intervention" heading is not read (only what is in one sentence or line).
Vocabulary NOT measured on real sites: label a sample before relying on it.
"""
from __future__ import annotations

import re
from typing import Optional

from fbdb.enrich.activity_rules import _HIRING, _PARTNER, fold, segments

NUMBER_WORDS = {"deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11,
                "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "trente": 30, "quarante": 40,
                "cinquante": 50}
_NUM = r"(\d{1,4}|" + "|".join(NUMBER_WORDS) + r")"
_STAFF = r"(?:personnes|salaries|collaborateurs|collaboratrices|techniciens|compagnons|artisans|employes|professionnels|ouvriers|membres|experts)"
_TEAM_A = re.compile(r"\b(?:equipe|effectifs?)\b[^.;\n]{0,40}?\b" + _NUM + r"\s+" + _STAFF + r"\b")
_TEAM_B = re.compile(r"\b" + _NUM + r"\s+(?:salaries|collaborateurs|collaboratrices|techniciens|compagnons|employes|ouvriers)\b")
_TEAM_QUAL = re.compile(r"(plus de|environ|pres de|une trentaine de|une vingtaine de)\s*(?:[a-z']+ ){0,1}$")
_CLIENT_STAFF = re.compile(r"\b(vos|leurs?) (salaries|collaborateurs|equipes|employes)\b|\bformation\b")
_NEGATED_AREA = re.compile(r"\bn.interven\w+ (pas|plus)\b|\bne (nous )?deplac\w+ pas\b|\bhors (de |du |des |d.)|\bsauf\b")
_AREA_CONTEXT = re.compile(r"\binterven|\bdeplac|\btravaill|\bchantiers?\b|\bzone\b|\bcouvr|\brayon\b|\bmobile\b|\bdisponible|\bdesservons|\bsecteur")
_PLACE_TRIGGERS = [
    re.compile(r"\binterven(?:ons|ir|ant|tions?)\b(?: [a-z']+){0,3}? (?:a|sur|dans|en|autour de|aux alentours de|pres de|vers) (.+)$"),
    re.compile(r"\bdeplac\w+ (?:a|sur|dans|en|autour de|jusqu.a) (.+)$"),
    re.compile(r"\bdesservons? (.+)$"),
    re.compile(r"\b(?:zones? d.interventions?|secteurs? d.interventions?|zones? geographiques?|zone de chalandise|rayon d.action)\b\s*[:\-]?\s*(.+)$"),
]
_RADIUS = re.compile(r"\brayon (?:de |d.)?(\d{1,3}) ?km(?: (?:autour (?:de |d.)?|a partir (?:de |d.)?|depuis |de |d.)\s*(.+))?$|\brayon (?:de |d.)?(\d{1,3}) ?km\b")
_SCOPE_NATIONAL = re.compile(r"\btoute la france\b|\bfrance entiere\b|\bpartout en france\b|\bsur tout le territoire( national)?\b|\bensemble du territoire\b|\bfrance metropolitaine\b")
_SCOPE_REGIONAL = re.compile(r"\btoute la region\b|\bsur toute la region\b|\btoute l.ile-?de-?france\b")
_LEADING = re.compile(r"^(?:(?:a|au|aux|sur|dans|en|le|la|les|l'|du|de|des|d'|autour de|aux alentours de|pres de|vers|jusqu'a)\s+)+", re.I)
_SPLIT_LIST = re.compile(r"\s*(?:,|;|/|\bet\b|\bou\b|\bainsi que\b|\bmais aussi\b)\s*", re.I)
_NOT_PLACES = {"france", "nous", "notre", "nos", "vous", "votre", "vos", "toute", "tous", "environs", "alentours", "region", "departement",
               "secteur", "zone", "clients", "particuliers", "professionnels", "chantiers"}
MAX_PLACES = 15


def _place_names(tail: str) -> list[str]:
    tail = re.split(r"[.!?\n]", tail, 1)[0]
    tail = re.split(r"\s(?:pour|afin|avec|selon|depuis|quel|quelle|qui|dont)\b", tail, 1)[0]
    out = []
    for part in _SPLIT_LIST.split(tail):
        name = _LEADING.sub("", part.strip(" .:-\"'")).strip(" .:-\"'")
        if name.count("(") > name.count(")"):
            name = name.rsplit("(", 1)[0].strip()
        elif name.count(")") > name.count("("):
            name = name.rsplit(")", 1)[0].strip()
        name = re.sub(r"\s+(?:et|ses|ses environs|et environs|et alentours)$", "", name, flags=re.I).strip()
        if not (2 <= len(name) <= 40):
            continue
        if not (name[0].isupper() or name[0].isdigit()):
            continue                                   # only what is written as a proper name or a department number
        if fold(name).split(" ")[0] in _NOT_PLACES or fold(name) in _NOT_PLACES:
            continue
        out.append(name)
    return out[:MAX_PLACES]


def _number(token: str) -> Optional[int]:
    return int(token) if token.isdigit() else NUMBER_WORDS.get(token)


def extract_declarations(markdown: str, url: str) -> list[dict]:
    """Declarations found on one page. Each: {kind, value, number, qualifier, passage, url, matched_text}. Pure."""
    found: dict[tuple[str, str], dict] = {}
    team_done = False

    def add(kind, value, seg, matched, number=None, qualifier=None):
        found.setdefault((kind, value), {"kind": kind, "value": value, "number": number, "qualifier": qualifier,
                                         "passage": seg if len(seg) <= 300 else seg[:297] + "...", "url": url, "matched_text": matched})

    for seg in segments(markdown):
        f = fold(seg)
        if _HIRING.search(f):
            continue
        # ---- team size declared by the site
        if not team_done and not _PARTNER.search(f) and not _CLIENT_STAFF.search(f):
            for rx in (_TEAM_A, _TEAM_B):
                m = rx.search(f)
                if m:
                    n = _number(m.group(1))
                    if n:
                        q = _TEAM_QUAL.search(f[max(0, m.start(1) - 20):m.start(1)])
                        qual = {"plus de": "plus_de", "environ": "environ", "pres de": "pres_de"}.get(q.group(1) if q else "", "exact")
                        add("team_size", str(n), seg, seg[m.start():m.end()], number=n, qualifier=qual)
                        team_done = True
                        break
        # ---- service area
        if _NEGATED_AREA.search(f) or not _AREA_CONTEXT.search(f):
            continue
        if _SCOPE_NATIONAL.search(f):
            m = _SCOPE_NATIONAL.search(f)
            add("service_area_scope", "national", seg, seg[m.start():m.end()])
        elif _SCOPE_REGIONAL.search(f):
            m = _SCOPE_REGIONAL.search(f)
            add("service_area_scope", "regional", seg, seg[m.start():m.end()])
        rm = _RADIUS.search(f)
        if rm:
            km = rm.group(1) or rm.group(3)
            center = _LEADING.sub("", seg[rm.start(2):rm.end(2)].strip(" .,;:")) if rm.group(2) else ""
            words = f"{km} km autour de {center}" if center else f"{km} km"
            add("service_area_radius_km", words, seg, seg[rm.start():rm.end()], number=int(km))
        for rx in _PLACE_TRIGGERS:
            m = rx.search(f)
            if m:
                for name in _place_names(seg[m.start(1):]):
                    add("service_area_place", name, seg, name)
                break
    return list(found.values())


def merge_declarations(per_page: list[list[dict]]) -> list[dict]:
    """Several pages (home first): the first page that states a (kind, value) keeps it; ONE team size per read (the first found)."""
    seen, out, team = set(), [], False
    for items in per_page:
        for it in items:
            if it["kind"] == "team_size":
                if team:
                    continue
                team = True
            k = (it["kind"], it["value"])
            if k not in seen:
                seen.add(k)
                out.append(it)
    return out
