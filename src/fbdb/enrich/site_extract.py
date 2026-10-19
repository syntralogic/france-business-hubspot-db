"""Extract phones, emails and named people from the pages of a PROVEN official website (pure functions, no network, no database).

Used by fbdb.enrich.website once the site is tied to the company by its SIREN. Nothing here invents a value: it only reads
what the page publishes, keeps the line it came from as the proof, and attaches a phone/email to a person ONLY when both
sit on the same line, or the contact sits on the line right after the person.

  extract_phones(markdown, links)  -> [{"value", "normalized", "use", "use_basis", "context"}]
  extract_emails(markdown, links)  -> [{"value", "normalized", "use", "use_basis", "context"}]   # use_basis: address_name | page_context | None
  extract_people(markdown)         -> [{"first_name", "last_name", "function", "line", "phones", "emails", "rule"}]
  contact_pages(base_url, links)   -> same-site contact / team pages worth one more scrape

People extraction is heuristic: a name next to a function keyword, or the legal-notice formulas ("Directeur de la publication :
Jean DUPONT"). Measure its precision on ~50 real sites before trusting the `function_category` it produces.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlparse

from fbdb.transform.normalize import normalize_email, normalize_phone_fr

CONTACT_HINTS = ("contact", "equipe", "team", "qui-sommes", "notre-equipe", "a-propos", "apropos", "about", "societe", "agence")

PHONE_RE = re.compile(r"(?<![\d+])(?:\+33|0033|0)[\s.\-]?(?:\(0\)[\s.\-]?)?[1-9](?:[\s.\-]?\d{2}){4}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
JUNK_EMAIL_DOMAINS = {"example.com", "example.org", "domain.com", "email.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
                      "yoursite.com", "votresite.fr", "monsite.fr", "nomdedomaine.fr", "test.com"}
JUNK_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

_USE_WORDS = [
    ("emergency", ("depannage", "urgence", "astreinte", "24h", "7j")),
    ("after_sales", ("sav", "apres-vente", "apres vente", "service client")),
    ("accounting", ("compta", "facturation", "factures")),
    ("recruitment", ("recrut", "emploi", "candidature", "rh@", "carriere")),
    ("sales", ("devis", "commercial", "vente", "contact commercial")),
    ("direction", ("direction", "gerant", "gerance", "dirigeant", "president", "pdg")),
    ("switchboard", ("standard", "accueil", "secretariat")),
]
_EMAIL_USE_BY_LOCAL = [("direction", ("direction", "gerant", "gerance", "dirigeant", "pdg", "president")),
                       ("sales", ("devis", "commercial", "vente", "ventes", "commerciale")),
                       ("accounting", ("compta", "comptabilite", "facturation")),
                       ("after_sales", ("sav", "service-client", "serviceclient", "support", "assistance")),
                       ("emergency", ("urgence", "urgences", "depannage", "astreinte")),
                       ("recruitment", ("recrutement", "rh", "emploi", "jobs", "candidature")),
                       ("general", ("contact", "info", "infos", "accueil", "hello", "bonjour", "mail", "secretariat", "administration"))]


def _fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch))


def _line_of(text: str, start: int, end: int) -> str:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return text[a: b if b != -1 else len(text)].strip()


def _use_from_context(line: str) -> str | None:
    f = _fold(line)
    for use, words in _USE_WORDS:
        if any(w in f for w in words):
            return use
    return None


def extract_phones(markdown: str, links: list[str] | None = None) -> list[dict]:
    """Every valid French phone written on the page or in a tel: link, de-duplicated by normalized value."""
    out: dict[str, dict] = {}
    text = markdown or ""
    for m in PHONE_RE.finditer(text):
        norm = normalize_phone_fr(m.group(0))
        if norm and norm not in out:
            line = _line_of(text, m.start(), m.end())
            use = _use_from_context(line)
            out[norm] = {"value": m.group(0).strip(), "normalized": norm, "use": use, "context": line[:200],
                         "use_basis": "page_context" if use else None}
    for link in links or []:
        if link.lower().startswith("tel:"):
            raw = link[4:].split("?")[0].replace("%20", " ")
            norm = normalize_phone_fr(raw)
            if norm and norm not in out:
                out[norm] = {"value": raw, "normalized": norm, "use": None, "context": link[:200], "use_basis": None}
    return list(out.values())


def _email_ok(norm: str) -> bool:
    domain = norm.rsplit("@", 1)[1]
    return domain not in JUNK_EMAIL_DOMAINS and not norm.endswith(JUNK_EMAIL_SUFFIX)


def email_use(norm: str) -> str | None:
    local = _fold(norm.split("@", 1)[0])
    for use, words in _EMAIL_USE_BY_LOCAL:
        if local in words or any(local.startswith(w + ".") or local.startswith(w + "-") for w in words):
            return use
    return None


def extract_emails(markdown: str, links: list[str] | None = None) -> list[dict]:
    """Every valid email written on the page or in a mailto: link; images, placeholders and template domains are dropped."""
    out: dict[str, dict] = {}
    text = markdown or ""
    candidates = [(m.group(0), _line_of(text, m.start(), m.end())) for m in EMAIL_RE.finditer(text)]
    for link in links or []:
        if link.lower().startswith("mailto:"):
            candidates.append((link[7:].split("?")[0], link))
    for raw, line in candidates:
        norm = normalize_email(raw)
        if norm and _email_ok(norm) and norm not in out:
            by_address = email_use(norm)
            use = by_address or _use_from_context(line)
            # how the function was decided (contact_use_basis): the address itself, or the words around it on the page
            out[norm] = {"value": raw, "normalized": norm, "use": use, "context": line[:200],
                         "use_basis": None if not use else ("address_name" if by_address else "page_context")}
    return list(out.values())


def contact_pages(base_url: str, links: list[str], limit: int = 2) -> list[str]:
    """Same-site contact / team pages, best hint first (the page itself is excluded)."""
    host = (urlparse(base_url).hostname or "").removeprefix("www.")
    scored, seen = [], set()
    for link in links or []:
        full = urljoin(base_url, link).split("#")[0]
        p = urlparse(full)
        if (p.hostname or "").removeprefix("www.") != host or full.rstrip("/") == base_url.rstrip("/") or full in seen:
            continue
        for rank, hint in enumerate(CONTACT_HINTS):
            if hint in p.path.lower():
                seen.add(full)
                scored.append((rank, full))
                break
    return [u for _, u in sorted(scored)][:limit]


# ---------------------------------------------------------------- people

_UP = "A-ZÀ-ÖØ-Þ"
_LOW = "a-zß-öø-ÿ"
_FIRST = rf"[{_UP}][{_LOW}]+(?:-[{_UP}][{_LOW}]+)?"
_LAST = rf"(?:[{_UP}][{_UP}'\-]{{1,}}|[{_UP}][{_LOW}]+(?:-[{_UP}][{_LOW}]+)?)"
NAME_RE = re.compile(rf"(?<![\w-])({_FIRST})\s+({_LAST})(?![\w])")
FUNCTION_RE = re.compile(
    r"(?i)(?<!\w)((?:co-?)?g[ée]rante?|pr[ée]sidente?|directeur(?:rice)?(?:\s+(?!de la publication)[\wéèêàç'’\-]+){0,3}|"
    r"responsable(?:\s+[\wéèêàç'’\-]+){0,3}|charg[ée]e?\s+d['’]affaires|conducteur(?:rice)?\s+de\s+travaux|"
    r"chef(?:fe)?\s+de\s+(?:chantier|projet|[ée]quipe)|commercial(?:e)?|comptable|assistante?(?:\s+[\wéèêàç\-]+){0,2}|"
    r"secr[ée]taire|technicien(?:ne)?(?:\s+[\wéèêàç\-]+){0,2}|fondateur|fondatrice|dirigeante?)(?!\w)")
LEGAL_NOTICE_RE = re.compile(
    rf"(?i:(directeur(?:rice)?\s+de\s+la\s+publication|responsable\s+de\s+la\s+publication|g[ée]rante?|pr[ée]sidente?|dirigeante?|"
    rf"repr[ée]sent[ée]e?\s+par))\s*[:\-–—]?\s*(?i:M\.|Mme|Monsieur|Madame|Mr)?\s*({_FIRST})\s+({_LAST})(?![\w])")
# words that look like names because they are capitalised in headings: never a first or last name
_STOP = {_fold(w) for w in (
    "Notre", "Nos", "Equipe", "Équipe", "Contact", "Contactez", "Nous", "Accueil", "Services", "Mentions", "Légales", "Societe", "Société",
    "Sarl", "Sas", "Eurl", "Sasu", "Directeur", "Directrice", "Responsable", "Chef", "Chargé", "Chargée", "Gérant", "Gérante",
    "Président", "Présidente", "Commercial", "Commerciale", "Comptable", "Assistant", "Assistante", "Secrétaire", "Technicien",
    "Fondateur", "Fondatrice", "Dirigeant", "Dirigeante", "Madame", "Monsieur", "Mme", "Rue", "Avenue", "Boulevard", "Place",
    "France", "Paris", "Devis", "Gratuit", "Demande", "Telephone", "Téléphone", "Email", "Mail", "Adresse", "Horaires", "Ouvert",
    "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche", "Conducteur", "Conducteurs", "Travaux", "Chantier",
    "Projet", "Affaires", "Publication", "Siret", "Siren", "Capital", "Tva", "Rcs", "Qui", "Sommes", "Propos", "Hébergeur", "Ovh",
    "Politique", "Confidentialité", "Conditions", "Générales", "Vente", "Ventes", "Service", "Client", "Clients", "Entreprise",
    "Artisan", "Électricien", "Electricien", "Plombier", "Maçon", "Menuisier", "Chauffagiste")}


# A FIRST name is never a place word, a trade / service noun, a brand or a company word: "Saint Etienne", "Chauffage Climatisation",
# "Daikin Premium", "Les Pins" are headings, towns or products, not people. (Found by stress-testing with typical page texts.)
_NOT_FIRST = {_fold(w) for w in (
    "Saint", "Sainte", "Les", "Le", "La", "L", "Mont", "Port", "Pont", "Bois", "Val", "Villeneuve", "Chateau", "Château", "Aix", "Bourg",
    "Ville", "Pays", "Region", "Région", "Departement", "Département", "Mairie", "Centre", "Zone", "Parc", "Residence", "Résidence",
    "Chauffage", "Climatisation", "Plomberie", "Electricite", "Électricité", "Installation", "Depannage", "Dépannage", "Renovation",
    "Rénovation", "Entretien", "Maintenance", "Construction", "Batiment", "Bâtiment", "Maison", "Salle", "Pompe", "Panneau", "Panneaux",
    "Solaire", "Energie", "Énergie", "Confort", "Expert", "Pro", "Premium", "Groupe", "Group", "Atelier", "Agence", "Cabinet", "Bureau",
    "Espace", "Air", "Eau", "Isolation", "Ventilation", "Toiture", "Couverture", "Peinture", "Carrelage", "Serrurerie", "Menuiserie",
    "Domotique", "Securite", "Sécurité", "Qualite", "Qualité", "Garantie", "Tarifs", "Realisations", "Réalisations", "Prestations",
    "Partenaires", "Temoignages", "Témoignages", "Avis", "Actualites", "Actualités", "Blog", "Galerie", "Zones", "Intervention",
    "Votre", "Vos", "Ma", "Mon", "Mes", "Ce", "Cette", "Ces", "Une", "Un", "Des", "Du", "De", "Et", "En", "Pour", "Avec", "Sans")}
# a LAST name is never one of these non-surname nouns (real surnames such as Charpentier, Boulanger, Fournier stay allowed)
_NOT_LAST = {_fold(w) for w in (
    "Climatisation", "Chauffage", "Plomberie", "Electricite", "Électricité", "Installation", "Depannage", "Dépannage", "Renovation",
    "Rénovation", "Entretien", "Maintenance", "Premium", "Pro", "Expert", "Energie", "Énergie", "Confort", "Eau", "Air", "Provence",
    "Qualite", "Qualité", "Garantie", "Securite", "Sécurité", "Domotique", "Solaire", "Services", "Groupe", "Group", "Isolation",
    "Ventilation", "Toiture", "Couverture", "Peinture", "Carrelage", "Serrurerie", "Menuiserie", "Habitat", "Immobilier", "Fenetres",
    "Fenêtres", "Portes", "Volets", "Tarifs", "Avis", "Realisations", "Réalisations", "Partenaires", "Prestations")}


def _brand_tokens() -> set[str]:
    from fbdb.enrich.activity_rules import BRANDS   # manufacturers named on sites: never a first name
    return {_fold(label.split()[0]) for _code, label, _rx in BRANDS}


_NOT_FIRST |= _brand_tokens()
# civility written before a name ("Mme Claire Petit"): removed so that it does not swallow the first name
_CIVILITY = re.compile(rf"(?<![\w-])(?:M\.|Mr\.?|Mme\.?|Mlle\.?|Monsieur|Madame|Mademoiselle|Dr\.?|Docteur|Ma[iî]tre)\s+(?=[{_UP}])")
# a line that credits somebody who is NOT the company's own team: web agency / hosting credits, an article or photo author, a
# testimonial (a customer who is "gérant" elsewhere), a partner / supplier / client. Never read for name + function.
_NOT_OWN_TEAM = re.compile(
    r"(?i)h[ée]berg|r[ée]alis[ée]e? par|cr[ée][ée]e? par|d[ée]velopp[ée]e? par|conception|webmaster|agence web|web ?design|©|copyright|"
    r"[ée]crit(?:e)? par|r[ée]dig[ée]e? par|\bauteur|photographe|cr[ée]dits?\b|t[ée]moign|avis (?:client|google)|\bclients?\b|"
    r"partenaires?|fournisseurs?|sous-?traitan|[«»\u201c\u201d]|ils nous (?:font|ont fait) confiance")
_TRAILING_WORDS = {"chez", "de", "du", "des", "la", "le", "les", "a", "au", "aux", "et", "ou", "d", "l", "en", "pour", "par", "sur"}


def _clean_name(first: str, last: str):
    if _fold(first) in _STOP or _fold(last) in _STOP or len(last) < 2:
        return None
    if _fold(first) in _NOT_FIRST or _fold(last) in _NOT_LAST:
        return None
    if last.isupper():
        last = "-".join(p.capitalize() for p in last.split("-"))
    return first, last


def _tidy_function(text: str, first: str = "", last: str = "") -> str:
    """The function as written, without the person's own name that the pattern may have swallowed, and without a trailing preposition."""
    t = re.sub(r"\s+", " ", text).strip(" -–—:;,.")
    for n in (first, last):
        if n:
            t = re.sub(rf"(?<![\w-]){re.escape(n)}(?![\w])", " ", t, flags=re.I)
    words = t.split()
    while words and _fold(words[-1].rstrip("'’")) in _TRAILING_WORDS:
        words.pop()
    return " ".join(words).strip(" -–—:;,.")


def _names_in(text: str) -> list[tuple[str, str]]:
    """Every (first, last) of the text that survives _clean_name. A rejected pair (\"Directeur Marc\") does not swallow the next word:
    scanning restarts on its second word, so \"Directeur Marc Leroy\" still gives Marc Leroy and a second name is never missed."""
    out, pos = [], 0
    while True:
        m = NAME_RE.search(text, pos)
        if not m:
            return out
        if _clean_name(m.group(1), m.group(2)):
            out.append((m.group(1), m.group(2)))
            pos = m.end()
        else:
            pos = m.start(2)


def _neighbour_ok(line: str) -> bool:
    """A neighbouring line may give a contact to the person only if it names nobody else and is not a switchboard/general line."""
    if _names_in(line):
        return False
    return _use_from_context(line) not in ("switchboard", "general", "sales", "accounting", "recruitment", "after_sales")


def extract_people(markdown: str) -> list[dict]:
    """Named people with their function, from the legal notice formulas and from name+function on the same/adjacent lines."""
    lines = [ln.strip() for ln in (markdown or "").splitlines()]
    plain = [_CIVILITY.sub("", ln) for ln in lines]            # names are searched without their civility ("Mme Claire Petit")
    people: dict[tuple[str, str], dict] = {}

    def add(first, last, function, line, rule, block):
        cleaned = _clean_name(first, last)
        if not cleaned:
            return
        key = (_fold(cleaned[0]), _fold(cleaned[1]))
        p = people.setdefault(key, {"first_name": cleaned[0], "last_name": cleaned[1], "function": None, "line": line[:240],
                                     "phones": [], "emails": [], "rule": rule})
        if function and not p["function"]:
            p["function"] = _tidy_function(function, cleaned[0], cleaned[1])
            p["line"], p["rule"] = line[:240], rule
        for ph in extract_phones(block):
            if ph["normalized"] not in p["phones"]:
                p["phones"].append(ph["normalized"])
        for em in extract_emails(block):
            if em["normalized"] not in p["emails"]:
                p["emails"].append(em["normalized"])

    for i, line in enumerate(lines):
        if not line:
            continue
        # contacts belong to the person they FOLLOW: own line + the next line (never the previous one, which is the person above)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        block = "\n".join([line] + ([nxt] if nxt and _neighbour_ok(plain[i + 1]) else []))
        for m in LEGAL_NOTICE_RE.finditer(line):
            add(m.group(2), m.group(3), m.group(1), line, "legal_notice", line)
        fm = FUNCTION_RE.search(plain[i])
        if not fm or _NOT_OWN_TEAM.search(line):               # credits, authors, testimonials, partners: not the company's own team
            continue
        # candidate names: same line first, else the previous / next non-empty line (never one that credits somebody else)
        around = [(plain[j], lines[j]) for j in (i - 1, i + 1) if 0 <= j < len(lines) and lines[j] and not _NOT_OWN_TEAM.search(lines[j])]
        for source, original in [(plain[i], line)] + around:
            names = _names_in(source)
            if len(names) == 1:
                # contacts count for the person only if the block holds exactly one person
                add(names[0][0], names[0][1], fm.group(1), line if source is plain[i] else original + " / " + line, "name_and_function", block)
                break
            if len(names) > 1:
                break
    # a contact is attached to a person only when it is unambiguous: drop values shared by several people
    for kind in ("phones", "emails"):
        count: dict[str, int] = {}
        for p in people.values():
            for v in p[kind]:
                count[v] = count.get(v, 0) + 1
        for p in people.values():
            p[kind] = [v for v in p[kind] if count[v] == 1]
    return list(people.values())
