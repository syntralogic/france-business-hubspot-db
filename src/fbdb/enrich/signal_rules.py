"""Realisations, partners and recruitment as a site writes them (client spec, section 12, part 2). Pure: no network, no database.

Three kinds of signal, each kept with the sentence that justifies it, the words matched and the page URL:
  realisation  a described project. Read ONLY on a page whose URL says "realisations / chantiers / references / projets / portfolio",
               and only a sentence that names a kind of work AND something specific (a year, a surface or power, a postcode, a place,
               "pour <un client>"). "Nous realisons tous types de travaux" is marketing, not a realisation. Photos and galleries are
               never read (images and alt text are removed): a picture of a worksite proves nothing.
  partner      a NAME listed under a partner / client / reference heading, or after "nos partenaires :", "ils nous font confiance :",
               "en sous-traitance pour :". The `relation` is the word the site uses; the direction of a relationship is never interpreted
               and the name is never matched to a company of our base (no SIREN is guessed). A logo bar is an image: not read.
  hiring       the site says it recruits ("nous recrutons un electricien", "offre d'emploi", "H/F" job line). A negation ("nous ne
               recrutons pas", "aucune offre d'emploi") is not a signal; neither is "rejoignez-nous sur Facebook".
All three are CLAIMS of the site, status `claimed`. Absence is never concluded here (see fbdb.enrich.site_signals).
The vocabulary is a first version made for building trades, NOT measured on real sites: label a sample before anyone targets on it.
"""
from __future__ import annotations

import hashlib
import re

from fbdb.enrich.activity_rules import _HIRING, RULES, _negated, _passage, clean, fold, segments

_TRADES = [(label, re.compile(rx)) for facet, _code, label, rx in RULES if facet == "trade"]

REALISATION_PAGE = ("realisation", "chantier", "reference", "portfolio", "projets", "nos-projets", "nos-travaux", "travaux-realises")
PARTNER_PAGE = ("partenaire", "marques", "fournisseur", "clients", "confiance", "nos-clients")
HIRING_PAGE = ("recrut", "emploi", "carriere", "offre-d", "offres-d", "rejoignez", "nous-rejoindre", "jobs", "job-", "postuler")
MAX_REALISATIONS = 10
MAX_PARTNERS = 30
MAX_HIRING = 5


def page_kind(url: str) -> str:
    """'realisation' | 'partner' | 'hiring' | 'other', from the path only. Order: hiring first (a careers page about projects is hiring)."""
    path = fold(re.sub(r"^https?://[^/]+", "", url or "")).lower().split("?")[0]
    for kind, hints in (("hiring", HIRING_PAGE), ("partner", PARTNER_PAGE), ("realisation", REALISATION_PAGE)):
        if any(h in path for h in hints):
            return kind
    return "other"


# ------------------------------------------------------------------ realisations
_WORK = re.compile(r"\b(chantiers?|realisations?|realise[es]?|projets?|renovation|installation|pose|remplacement|construction|amenagement|"
                   r"mise aux normes|creation|extension|rehabilitation|refection|raccordement|equipement|mise en place)\b")
_SPECIFIC = re.compile(r"\b(19|20)\d\d\b|\b\d+([.,]\d+)? ?(m2|m²|kw|kwc|kva|logements?|appartements?|lots?)\b|\b\d{5}\b|"
                       r"\bpour (un|une|le|la|l.|les|m\.|mme|monsieur|madame|la commune|la mairie|la ville|nos clients)\b|"
                       r"(?<![a-zé])(a|à|au|aux|en|sur) [A-ZÉÈ][a-zéèêàç-]+")
_NOT_A_PROJECT = re.compile(r"\b(voir (tous|toutes|plus)|en savoir plus|decouvrir|cliquez|lire la suite|nos realisations|toutes nos|"
                            r"tous types de|toutes sortes|tout type de|quel que soit|n.hesitez pas|contactez|demandez)\b")
_YEAR = re.compile(r"\b((?:19|20)\d\d)\b")


def _sha(text: str) -> str:
    return hashlib.sha1(fold(" ".join(text.split())).encode("utf-8")).hexdigest()[:12]


def extract_realisations(markdown: str, url: str) -> list[dict]:
    out, seen = [], set()
    for seg in segments(markdown):
        if len(out) >= MAX_REALISATIONS:
            break
        f = fold(seg)
        # "specific" is tested on the ORIGINAL text: a capitalised place name is what makes "a Lyon" specific
        if len(seg) < 40 or len(seg.split()) < 5 or _HIRING.search(f) or _NOT_A_PROJECT.search(f):
            continue
        m = _WORK.search(f)
        if not m or _negated(f, m.start()) or not (_SPECIFIC.search(seg) or _SPECIFIC.search(f)):
            continue
        code = "r:" + _sha(seg)
        if code in seen:
            continue
        seen.add(code)
        y = _YEAR.search(seg)
        out.append({"kind": "realisation", "code": code, "name": None, "relation": None, "year_mentioned": int(y.group(1)) if y else None,
                    "matched_text": seg[m.start():m.end()], "passage": _passage(seg, m.start(), m.end()), "url": url})
    return out


# ------------------------------------------------------------------ partners
_HEADING = re.compile(r"^(nos |vos |les )?(partenaires?|references?( clients?)?|clients?|marques( partenaires)?|fournisseurs?|"
                      r"ils nous (font|ont fait) confiance|nous faisons confiance|ils nous accompagnent|nos realisations pour)\b.{0,25}:?$")
_RELATION_OF_HEAD = (("partenaire", "partner"), ("marque", "supplier_brand"), ("fournisseur", "supplier_brand"),
                     ("client", "client_reference"), ("reference", "client_reference"), ("confiance", "client_reference"),
                     ("accompagn", "client_reference"))
_INLINE = re.compile(r"\b(nos partenaires?|partenaires?|marques partenaires|nos marques|nos fournisseurs|ils nous (?:font|ont fait) confiance|"
                     r"nos clients?|nos references|en sous-?traitance (?:pour|de|aupres de)|nous travaillons (?:avec|pour)|"
                     r"nous intervenons (?:pour|aupres de)|ils nous accompagnent)\s*(?:sont|:|-)?\s*:?\s*(?P<list>.+)$")
_STOP = {"nous", "notre", "nos", "vous", "votre", "vos", "contact", "contactez", "accueil", "voir", "decouvrir", "lire", "en savoir",
         "plus", "tous", "toutes", "les", "des", "un", "une", "et", "devis", "gratuit", "menu", "retour", "haut", "suite", "cliquez",
         "particuliers", "professionnels", "entreprises", "clients", "partenaires", "references", "marques", "fournisseurs", "etc"}
_SPLIT_LIST = re.compile(r"\s*(?:[,;|/•·]|\s-\s|\bet\b|\bainsi que\b)\s*")


def _relation(text: str) -> str:
    f = fold(text)
    if "sous" in f and "trait" in f:
        return "subcontracting"
    for key, rel in _RELATION_OF_HEAD:
        if key in f:
            return rel
    return "partner"


def _names(raw: str) -> list[str]:
    out = []
    for item in _SPLIT_LIST.split(raw.strip(" .:-")):
        item = item.strip(" .:-\"'«»()")
        words = item.split()
        if not (2 <= len(item) <= 50) or len(words) > 5 or not (item[0].isupper() or item[0].isdigit()):
            continue
        if fold(item) in _STOP or fold(words[0]) in _STOP:
            continue
        if item.isupper() and len(item) > 25:
            continue
        out.append(item)
    return out


_LINK_ONLY = re.compile(r"^[\s\-*+•>#]*\[[^\]]*\]\([^)]*\)[\s*]*$")
# words of a site menu: a block that contains one of them is a navigation menu, not a list of partners
_NAV = {"accueil", "contact", "contactez nous", "services", "nos services", "prestations", "realisations", "nos realisations", "metiers",
        "a propos", "qui sommes nous", "equipe", "blog", "actualites", "recrutement", "carrieres", "devis", "tarifs", "avis", "galerie",
        "zone d intervention", "entreprise", "societe", "produits", "mentions legales", "partenaires", "clients", "references", "marques",
        "plan du site", "faq", "contactez-nous", "menu", "rechercher", "connexion", "mon compte", "panier", "boutique"}


def _norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", fold(text)).strip()


def extract_partners(markdown: str, url: str, page: str = "other") -> list[dict]:
    """Names listed as partner / client / reference / supplier, with the real lines that say it. A heading that is itself a link
    is a menu entry and starts nothing; a block of short lines that holds a menu word (Accueil, Contact, Realisations...) is a menu."""
    lines, link_only = [], []
    for raw in (markdown or "").splitlines():
        line = " ".join(clean(raw).replace(" ", " ").split()).lstrip("-•·–—+ ").strip()
        if line:
            lines.append(line)
            link_only.append(bool(_LINK_ONLY.match(raw)) and not raw.lstrip().startswith("#"))
    found: dict[str, dict] = {}

    def add(name, relation, passage, matched):
        code = "n:" + _norm_name(name)
        if len(found) < MAX_PARTNERS and code != "n:" and code not in found:
            found[code] = {"kind": "partner", "code": code, "name": name, "relation": relation, "year_mentioned": None,
                           "matched_text": matched, "passage": _passage(passage, 0, min(len(passage), 80)), "url": url}

    i = 0
    while i < len(lines):
        line = lines[i]
        f = fold(line)
        if len(line) <= 70 and _HEADING.match(f) and not re.search(r"[.!?]$", line) and not link_only[i]:
            rel = _relation(line)
            head_names = _names(line.split(":", 1)[1]) if ":" in line else []
            block, j = [], i + 1
            while j < len(lines) and len(block) < MAX_PARTNERS and len(lines[j]) <= 50 and not _HEADING.match(fold(lines[j])):
                block.append(lines[j])
                j += 1
            if any(_norm_name(l) in _NAV for l in block):          # a menu, not a list of partners
                block, j = [], i + 1
            for n in head_names:
                add(n, rel, line, n)
            for l in block:
                for n in _names(l):
                    add(n, rel, f"{line} > {l}", n)                  # two real lines of the page, joined by ">"
            i = j if block else i + 1
            continue
        m = _INLINE.search(f)
        if m and not _negated(f, m.start()) and len(line) <= 400:
            for n in _names(line[m.start("list"):]):
                add(n, _relation(f[m.start():m.start("list")]), line, n)
        i += 1
    return list(found.values())


# ------------------------------------------------------------------ hiring
_HIRING_STRONG = re.compile(r"\brecrut\w*|\boffres? d.emploi\b|\bpostes? a pourvoir\b|\bnous cherchons\b|\brejoignez[- ]nous\b|"
                            r"\bnous rejoindre\b|\bh ?/ ?f\b|\bf ?/ ?h\b|\bh ?- ?f\b")
_HIRING_NEG = re.compile(r"\bn[e'] ?[^.;]{0,25}\brecrut\w*\s+(pas|plus|rien|actuellement pas)\b|\bpas (de |d.)(poste|offre|recrutement)s?\b|"
                         r"\b(aucun|aucune|plus d.)\s*(poste|offre|recrutement)s?\b|\bplus de (poste|offre)s?\b")
_SOCIAL = re.compile(r"\b(facebook|instagram|linkedin|tiktok|youtube|reseaux|newsletter|twitter)\b")
_CONTRACT = (("cdi", r"\bcdi\b"), ("cdd", r"\bcdd\b"), ("alternance", r"\balternan\w*|\bapprenti\w*|\bcontrat pro\w*"),
             ("interim", r"\binterim\b|\bmission temporaire\b"), ("stage", r"\bstages?\b"))
_ROLE_EXTRA = (("chef de chantier", r"\bchefs? de chantier\b"), ("conducteur de travaux", r"\bconducteurs? de travaux\b"),
               ("technicien", r"\btechniciens?\b"), ("apprenti", r"\bapprentis?\b"), ("compagnon", r"\bcompagnons?\b"),
               ("assistant(e)", r"\bassistante?\b|\bsecretaire\b"), ("commercial", r"\bcommerciale?s?\b|\bchargee? d.affaires\b"),
               ("ouvrier", r"\bouvriers?\b|\bmanoeuvres?\b"))


def _role(f: str) -> str | None:
    for label, rx in _TRADES:
        if rx.search(f):
            return label.split(" /")[0]
    for label, rx in _ROLE_EXTRA:
        if re.search(rx, f):
            return label[0].upper() + label[1:]
    return None


def extract_hiring(markdown: str, url: str, page: str = "other") -> list[dict]:
    out: dict[str, dict] = {}
    for seg in segments(markdown):
        f = fold(seg)
        m = _HIRING_STRONG.search(f)
        if not m or _HIRING_NEG.search(f) or _SOCIAL.search(f):
            continue
        weak_only = m.group(0).replace(" ", "") in ("h/f", "f/h", "h-f", "recrutement")
        if weak_only and page != "hiring":      # an "H/F" marker is read on a recruitment page only
            continue
        if page != "hiring" and m.group(0) in ("rejoignez-nous", "rejoignez nous", "nous rejoindre"):
            continue                              # a bare "rejoignez-nous" outside a recruitment page is a slogan
        role = _role(f)
        contract = next((c for c, rx in _CONTRACT if re.search(rx, f)), None)
        code = "role:" + fold(role or "generic")
        if code not in out and len(out) < MAX_HIRING:
            out[code] = {"kind": "hiring", "code": code, "name": role, "relation": contract, "year_mentioned": None,
                         "matched_text": seg[m.start():m.end()], "passage": _passage(seg, m.start(), m.end()), "url": url}
    return list(out.values())


def extract_signals(markdown: str, url: str, page: str | None = None) -> list[dict]:
    """All signals of one page. `page` = 'home' or page_kind(url). Realisations only on a realisation page, partners on a partner
    page or from an explicit sentence anywhere, hiring anywhere (with the stricter rules above)."""
    page = page or page_kind(url)
    items = []
    if page == "realisation":
        items += extract_realisations(markdown, url)
    items += extract_partners(markdown, url, page)
    items += extract_hiring(markdown, url, page)
    return items


def merge_pages(per_page: list[list[dict]]) -> list[dict]:
    """Several pages (home first): the first page that states a (kind, code) keeps it. Caps are applied per kind."""
    seen, out, per_kind = set(), [], {}
    caps = {"realisation": MAX_REALISATIONS, "partner": MAX_PARTNERS, "hiring": MAX_HIRING}
    for items in per_page:
        for it in items:
            k = (it["kind"], it["code"])
            if k in seen or per_kind.get(it["kind"], 0) >= caps[it["kind"]]:
                continue
            seen.add(k)
            per_kind[it["kind"]] = per_kind.get(it["kind"], 0) + 1
            out.append(it)
    return out
