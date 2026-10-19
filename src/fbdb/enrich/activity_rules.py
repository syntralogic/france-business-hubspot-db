"""Classification of what a company says it does, from the text of its own site (client spec, section 3). Pure: no network, no database.

A classification is a KEYWORD rule applied to a sentence or a menu line of the page. Each result keeps the passage that justifies
it, the words that matched and the page URL, so a person can check it. Nothing is deduced: a rule fires only on what the page
writes, and the following are never read as a claim:
  - images: `![alt](url)` is removed before reading, so a photo (or its alt text) is never a proof of experience or of a service;
  - a sentence with a negation just before the keyword ("nous ne faisons pas de climatisation", "plus de depannage");
  - a recruitment sentence ("nous recrutons un electricien") and, for trade / service / specialty / work type, a sentence about a
    partner or sub-contractor ("nous travaillons avec un plombier partenaire") or a training offer.
Blog / news / legal / hiring pages are not read (see site_activity.activity_pages): an article about heat pumps is not a service.

Also: building_type (kind of building / site the text says it works on) and commitment (delay, free quote, guarantee, support,\nspecialisation announced). Both are claims of the site, like every facet.\nFacets: trade (metier reel), service (prestations), specialty (IRVE, clim, photovoltaique...), work_type (neuf / renovation /
depannage / maintenance), client_type, contract (entretien, urgence 24/7), brand (a manufacturer named on the page), approval
(agrement / label CLAIMED on the site; an official certification stays in company_certifications).

The vocabulary is a first version made for building trades (electricity, plumbing, heating, air conditioning, roofing...): it has NOT
been measured on real sites (no live access from the build sandbox). Extend the tables below, and label a sample before targeting on it.
"""
from __future__ import annotations

import re
import unicodedata

# (facet, code, label, regex on the accent-folded lowercase text)
RULES: list[tuple[str, str, str, str]] = [
    # ---- trade: the occupation the company says it practises
    ("trade", "electricien", "Electricien", r"\belectriciens?\b|\belectricite generale\b|\bentreprise d.electricite\b"),
    ("trade", "plombier", "Plombier", r"\bplombiers?\b|\bplomberie\b"),
    ("trade", "chauffagiste", "Chauffagiste", r"\bchauffagistes?\b|\binstallateurs? de chauffage\b|\b(entreprise|societe) de chauffage\b"),
    ("trade", "climaticien", "Climaticien / frigoriste", r"\bclimaticiens?\b|\bfrigoristes?\b|\bgenie climatique\b|\bfroid et climatisation\b"),
    ("trade", "couvreur", "Couvreur / zingueur", r"\bcouvreurs?\b|\bzingueurs?\b|\bzinguerie\b|\btravaux de toiture\b|\bcouverture et zinguerie\b"),
    ("trade", "macon", "Macon / gros oeuvre", r"\bmaconnerie\b|\bmacons?\b|\bgros oeuvre\b"),
    ("trade", "menuisier", "Menuisier", r"\bmenuisiers?\b|\bmenuiserie\b"),
    ("trade", "peintre", "Peintre en batiment", r"\bpeintres? en batiment\b|\bentreprise de peinture\b|\bpeinture (interieure|exterieure)\b|\bpeintres?\b"),
    ("trade", "plaquiste", "Plaquiste / platrier", r"\bplaquistes?\b|\bplaquisterie\b|\bplatrerie\b|\bplatriers?\b|\bcloisons? seches?\b"),
    ("trade", "carreleur", "Carreleur", r"\bcarreleurs?\b|\bcarrelage\b"),
    ("trade", "charpentier", "Charpentier", r"\bcharpentiers?\b|\bcharpente\b"),
    ("trade", "facadier", "Facadier / ravalement", r"\bfacadiers?\b|\bravalement\b"),
    ("trade", "serrurier", "Serrurier", r"\bserruriers?\b|\bserrurerie\b"),
    # ---- service: the prestations
    ("service", "installation", "Installation / pose", r"\binstallations?\b|\binstalle[rz]?\b|\binstallons\b|\bpose (de|d.)"),
    ("service", "remplacement", "Remplacement", r"\bremplacements?\b|\brenouvellement\b|\bchangement (de|d.)"),
    ("service", "mise_aux_normes", "Mise aux normes / conformite", r"\bmise aux normes\b|\bmise en conformite\b|\bnf ?c ?15-?100\b"),
    ("service", "diagnostic", "Diagnostic", r"\bdiagnostics?\b"),
    ("service", "sav", "Service apres-vente", r"\bservice apres-?vente\b|\bsav\b"),
    ("service", "ramonage", "Ramonage", r"\bramonage\b"),
    ("service", "desembouage", "Desembouage", r"\bdesembouage\b"),
    ("service", "detection_fuite", "Recherche de fuite", r"\b(recherche|detection) de fuites?\b"),
    # ---- specialty: technical niches
    ("specialty", "irve", "IRVE / bornes de recharge",
     r"\birve\b|\bbornes? (de|pour) (recharge|vehicules? electriques?)\b|\brecharge (de )?vehicules? electriques?\b|\bwallbox\b"),
    ("specialty", "climatisation", "Climatisation", r"\bclimatisation\b|\bclimatiseurs?\b|\bclim\b|\bmulti-?split\b"),
    ("specialty", "pompe_a_chaleur", "Pompe a chaleur", r"\bpompes? a chaleur\b|\bpac\b|\baerothermie\b|\bgeothermie\b"),
    ("specialty", "photovoltaique", "Photovoltaique / solaire", r"\bphotovoltaique\b|\bpanneaux? solaires?\b|\bautoconsommation\b|\bsolaire\b"),
    ("specialty", "domotique", "Domotique", r"\bdomotique\b|\bmaisons? connectee\b|\bmaisons? intelligente\b|\bsmart home\b|\bknx\b"),
    ("specialty", "ventilation", "Ventilation / VMC", r"\bvmc\b|\bventilation\b"),
    ("specialty", "renovation_energetique", "Renovation energetique",
     r"\brenovation energetique\b|\bma ?prime ?renov\b|\baudit energetique\b|\bcertificats? d.economies? d.energie\b"),
    ("specialty", "poele_cheminee", "Poele / cheminee", r"\bpoeles?\b|\bcheminees?\b|\bchaudieres? (a |au )?(bois|granules)\b"),
    ("specialty", "chaudiere", "Chaudiere", r"\bchaudieres?\b"),
    ("specialty", "traitement_eau", "Traitement de l'eau", r"\badoucisseurs?\b|\btraitement de l.eau\b|\bosmoseurs?\b"),
    ("specialty", "alarme_securite", "Alarme / securite", r"\balarmes?\b|\bvideosurveillance\b|\bcontrole d.acces\b|\bvisiophone\b|\binterphone\b"),
    ("specialty", "courants_faibles", "Courants faibles / VDI", r"\bcourants? faibles?\b|\bvdi\b|\breseaux? informatiques?\b|\bfibre optique\b"),
    ("specialty", "salle_de_bain", "Salle de bain", r"\bsalles? de bains?\b|\bdouche a l.italienne\b"),
    ("specialty", "isolation", "Isolation", r"\bisolation\b|\bisolants?\b|\bcalorifug"),
    ("specialty", "eclairage", "Eclairage", r"\beclairages?\b"),
    # ---- work type
    ("work_type", "neuf", "Neuf",
     r"\bconstructions? neuves?\b|\btravaux neufs?\b|\binstallations? neuves?\b|\b(batiments?|logements?|maisons?) neufs?\b|\bdans le neuf\b|\ben neuf\b|\bpour le neuf\b"
     r"|\bneuf (et|ou|&|/) ?(la |de la )?renovation\b|\brenovation (et|ou|&|/) ?(le )?neuf\b"),
    ("work_type", "renovation", "Renovation", r"\brenovations?\b|\brenover\b|\brehabilitation\b|\bremise a neuf\b"),
    ("work_type", "depannage", "Depannage", r"\bdepannages?\b|\bdepanner\b|\bdepannons\b|\bintervention d.urgence\b"),
    ("work_type", "maintenance", "Maintenance / entretien", r"\bmaintenance\b|\bentretien\b"),
    # ---- client type
    ("client_type", "particuliers", "Particuliers", r"\bparticuliers?\b|\bresidentiel\b|\bmaisons? individuelles?\b|\bhabitat individuel\b"),
    ("client_type", "professionnels", "Professionnels", r"\bclients? professionnels?\b|\baux professionnels\b|\bparticuliers? et professionnels\b|\bprofessionnels? et particuliers\b|\bb2b\b|\bcommerces?\b"),
    ("client_type", "tertiaire", "Tertiaire", r"\btertiaire\b|\bbureaux\b|\blocaux professionnels\b"),
    ("client_type", "industrie", "Industrie", r"\bindustrie\b|\bindustriel(le)?s?\b"),
    ("client_type", "collectivites", "Collectivites / administrations", r"\bcollectivites?\b|\bmairies?\b|\badministrations?\b|\betablissements? publics?\b"),
    ("client_type", "syndics_copropriete", "Syndics / coproprietes", r"\bsyndics?\b|\bcoproprietes?\b"),
    ("client_type", "bailleurs_sociaux", "Bailleurs sociaux", r"\bbailleurs? sociaux\b|\blogements? sociaux\b|\bhlm\b"),
    ("client_type", "promoteurs_maitrise_oeuvre", "Promoteurs / maitrise d'oeuvre",
     r"\bpromoteurs?\b|\bconstructeurs?\b|\bmaitres? d.oeuvre\b|\bmaitrise d.oeuvre\b|\barchitectes?\b|\bbureaux? d.etudes?\b"),
    # ---- building type: what kind of building / site the text says it works on (a claim, never read from a photo)
    ("building_type", "maison_individuelle", "Maison individuelle", r"\bmaisons? individuelles?\b|\bpavillons?\b|\bvillas?\b|\bhabitat individuel\b"),
    ("building_type", "immeuble_collectif", "Immeuble / copropriete", r"\bimmeubles?\b|\bcoproprietes?\b|\bhabitat collectif\b|\bresidences? (collectives?|de logements)\b"),
    ("building_type", "appartement", "Appartement", r"\bappartements?\b"),
    ("building_type", "commerce", "Commerce / magasin", r"\bcommerces?\b|\bmagasins?\b|\bboutiques?\b|\bsurfaces? commerciales?\b"),
    ("building_type", "bureaux", "Bureaux / locaux tertiaires", r"\bbureaux\b|\blocaux (professionnels|tertiaires|commerciaux)\b"),
    ("building_type", "local_industriel", "Industrie / entrepot", r"\bsites? industriels?\b|\busines?\b|\bentrepots?\b|\bateliers?\b|\bbatiments? industriels?\b"),
    ("building_type", "erp", "ERP / batiment recevant du public", r"\berp\b|\betablissements? recevant du public\b"),
    ("building_type", "hotel_restaurant", "Hotel / restaurant", r"\bhotels?\b|\brestaurants?\b|\bcafes?-hotels?-restaurants?\b|\bchr\b"),
    ("building_type", "agricole", "Batiment agricole", r"\bbatiments? agricoles?\b|\bexploitations? agricoles?\b|\bhangars? agricoles?\b"),
    ("building_type", "batiment_public", "Batiment public / ecole / sante", r"\bbatiments? publics?\b|\becoles?\b|\bcollèges?\b|\bcolleges?\b|\bhopitaux\b|\bcliniques?\b|\bmairies?\b|\bsalles? des fetes\b"),
    # ---- commitment: what the company promises (delays, free quote, guarantee, support, specialisation)
    ("commitment", "delai", "Delai / rapidite annonces",
     r"\bsous (24|48|72) ?(h|heures)\b|\bdans les (24|48|72) ?(h|heures)\b|\bdelais? (courts?|rapides?|respectes?|tenus?)\b|\bintervention rapide\b|\bintervenons? rapidement\b|\breactivite\b|\bsous 1 semaine\b"),
    ("commitment", "devis_gratuit", "Devis gratuit", r"\bdevis (gratuits?|offerts?|sans engagement)\b|\bdevis gratuit\b"),
    ("commitment", "garantie", "Garantie annoncee", r"\bgaranti(e|es|s)?\b"),
    ("commitment", "accompagnement", "Accompagnement / suivi", r"\baccompagn(e|ons|ement|er|ent)\b|\bsuivi (de|du|des) (chantier|dossier|travaux)\b|\bconseils? personnalises?\b|\binterlocuteur unique\b"),
    ("commitment", "specialisation", "Specialisation annoncee", r"\bspecialises? (dans|en)\b|\bspecialistes? (de|du|des|en)\b|\bnotre specialite\b|\bexpert(s|e|es)? (en|de|du|des)\b"),
    # ---- contract
    ("contract", "contrat_entretien", "Contrat d'entretien / maintenance", r"\bcontrats? (annuels? )?d.entretien\b|\bcontrats? (annuels? )?de maintenance\b|\bcontrats? annuels?\b|\bcontrats? de service\b"),
    ("contract", "astreinte_urgence", "Urgence / astreinte 24h-7j",
     r"\b24 ?h? ?/ ?24 ?h?\b|\b7 ?j? ?/ ?7 ?j?\b|\b7 jours sur 7\b|\b24 heures sur 24\b|\bastreintes?\b|\burgences? 24\b|\bjour et nuit\b|\bnuit et jour\b"),
    # ---- approval CLAIMED on the site (not an official certification)
    ("approval", "rge", "RGE (indique sur le site)", r"\brge\b|\breconnu garant de l.environnement\b"),
    ("approval", "qualibat", "Qualibat (indique sur le site)", r"\bqualibat\b"),
    ("approval", "qualifelec", "Qualifelec (indique sur le site)", r"\bqualifelec\b"),
    ("approval", "qualipac", "QualiPAC (indique sur le site)", r"\bqualipac\b"),
    ("approval", "qualipv", "QualiPV (indique sur le site)", r"\bqualipv\b"),
    ("approval", "qualibois", "Qualibois (indique sur le site)", r"\bqualibois\b"),
    ("approval", "qualisol", "Qualisol (indique sur le site)", r"\bqualisol\b"),
    ("approval", "qualit_enr", "Qualit'EnR (indique sur le site)", r"\bqualit.?enr\b"),
    ("approval", "handibat", "Handibat (indique sur le site)", r"\bhandibat\b"),
    ("approval", "ecoartisan", "Eco Artisan (indique sur le site)", r"\beco-? ?artisans?\b"),
    ("approval", "iso_9001", "ISO 9001 (indique sur le site)", r"\biso ?9001\b"),
]

# (code, label, regex): a manufacturer named on the page. Short or common words (LG, ABB, Atlantic as a place) are left out on purpose.
BRANDS: list[tuple[str, str, str]] = [
    ("daikin", "Daikin", r"\bdaikin\b"), ("atlantic", "Atlantic", r"\batlantic\b"),
    ("mitsubishi_electric", "Mitsubishi Electric", r"\bmitsubishi\b"), ("viessmann", "Viessmann", r"\bviessmann\b"),
    ("saunier_duval", "Saunier Duval", r"\bsaunier duval\b"), ("de_dietrich", "De Dietrich", r"\bde dietrich\b"),
    ("vaillant", "Vaillant", r"\bvaillant\b"), ("bosch", "Bosch", r"\bbosch\b"), ("frisquet", "Frisquet", r"\bfrisquet\b"),
    ("chaffoteaux", "Chaffoteaux", r"\bchaffoteaux\b"), ("elm_leblanc", "Elm Leblanc", r"\belm leblanc\b"),
    ("ariston", "Ariston", r"\bariston\b"), ("buderus", "Buderus", r"\bbuderus\b"), ("nibe", "Nibe", r"\bnibe\b"),
    ("thermor", "Thermor", r"\bthermor\b"), ("hitachi", "Hitachi", r"\bhitachi\b"), ("panasonic", "Panasonic", r"\bpanasonic\b"),
    ("toshiba", "Toshiba", r"\btoshiba\b"), ("legrand", "Legrand", r"\blegrand\b"),
    ("schneider_electric", "Schneider Electric", r"\bschneider\b"), ("hager", "Hager", r"\bhager\b"),
    ("somfy", "Somfy", r"\bsomfy\b"), ("delta_dore", "Delta Dore", r"\bdelta dore\b"), ("velux", "Velux", r"\bvelux\b"),
    ("grundfos", "Grundfos", r"\bgrundfos\b"), ("grohe", "Grohe", r"\bgrohe\b"), ("hansgrohe", "Hansgrohe", r"\bhansgrohe\b"),
    ("geberit", "Geberit", r"\bgeberit\b"), ("wallbox", "Wallbox", r"\bwallbox\b"), ("easee", "Easee", r"\beasee\b"),
    ("zaptec", "Zaptec", r"\bzaptec\b"), ("solaredge", "SolarEdge", r"\bsolaredge\b"), ("enphase", "Enphase", r"\benphase\b"),
    ("sunpower", "SunPower", r"\bsunpower\b"),
]
AGREEMENT_WORDS = re.compile(r"\b(agree|agrement|certifie|partenaire officiel|installateur officiel|installateur premium|"
                             r"revendeur officiel|concessionnaire|distributeur agree|expert [a-z]+ certifie)\b")
_FACETS_WITH_PARTNER_GUARD = {"trade", "service", "specialty", "work_type"}
_HIRING = re.compile(r"\brecrut|\boffres? d.emploi\b|\bcdi\b|\bcdd\b|\balternan|\bapprenti|\bcandidatures?\b|\bposte a pourvoir\b|\bnous cherchons\b|\brejoignez[- ]nous\b")
_PARTNER = re.compile(r"\bpartenaires?\b|\bsous-?traita|\bconfreres?\b|\bcollaborons avec\b|\bfait(es)? appel a\b|\bfaisons appel a\b")
_TRAINING = re.compile(r"\bformations?\b|\bstages?\b|\borganisme de formation\b")
# A negation counts only when it sits right BEFORE the keyword ("ne faisons pas de plomberie ni de <keyword>", "plus de <keyword>"),
# never somewhere earlier in the sentence ("plus de 20 ans d'experience en <keyword>" is a claim, not a negation).
_NEGATION_TAIL = re.compile(r"(\bne\b[^.;]{0,30}\bpas\b[^.;]{0,30}|\bn'[a-z]+ pas\b[^.;]{0,30}|\b(pas|plus) d(e|u|')\s*(?:[a-z]+ ){0,2}|"
                            r"\baucune? (?:[a-z]+ ){0,2}|\bnon propos\w* (?:[a-z]+ ){0,2})$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_SPLIT = re.compile(r"(?<=[.!?;])\s+")
_COMPILED = [(f, c, lab, re.compile(rx)) for f, c, lab, rx in RULES]
_BRANDS = [(c, lab, re.compile(rx)) for c, lab, rx in BRANDS]
MAX_PASSAGE = 300


def _fold1(ch: str) -> str:
    """One character, lowercase, without accent, ALWAYS one character: indexes of the folded text are those of the original."""
    f = "".join(c for c in unicodedata.normalize("NFKD", ch.lower()) if not unicodedata.combining(c))
    return f if len(f) == 1 else ch.lower()[:1] or " "


def fold(text: str) -> str:
    return "".join(_fold1(c) for c in text)


def clean(markdown: str) -> str:
    """Page text for reading: no image (so no alt text), link text kept without its URL, no bare URL, no markdown decoration."""
    t = _IMAGE.sub(" ", markdown or "")
    t = _LINK.sub(r"\1", t)
    t = _URL.sub(" ", t)
    return re.sub(r"[#*>`_]+", " ", t)


def segments(markdown: str) -> list[str]:
    out = []
    for line in clean(markdown).splitlines():
        line = " ".join(line.replace(" ", " ").split())
        if len(line) < 3:
            continue
        out.extend(p.strip() for p in _SPLIT.split(line) if len(p.strip()) >= 3)
    return out


def _passage(segment: str, start: int, end: int) -> str:
    if len(segment) <= MAX_PASSAGE:
        return segment
    a = max(0, start - (MAX_PASSAGE - (end - start)) // 2)
    b = min(len(segment), a + MAX_PASSAGE)
    return ("..." if a else "") + segment[a:b].strip() + ("..." if b < len(segment) else "")


def _negated(folded: str, start: int) -> bool:
    return bool(_NEGATION_TAIL.search(folded[max(0, start - 70):start]))


def extract_activities(markdown: str, url: str) -> list[dict]:
    """Every claim found on this page, first sentence per (facet, code). Each item:
    {"facet", "code", "label", "matched_text", "passage", "url"}. Pure."""
    found: dict[tuple[str, str], dict] = {}

    def add(facet, code, label, seg, m):
        found.setdefault((facet, code), {"facet": facet, "code": code, "label": label, "matched_text": seg[m.start():m.end()],
                                        "passage": _passage(seg, m.start(), m.end()), "url": url})

    for seg in segments(markdown):
        f = fold(seg)
        if _HIRING.search(f):
            continue
        guarded = bool(_PARTNER.search(f) or _TRAINING.search(f))
        for facet, code, label, rx in _COMPILED:
            if (facet, code) in found or (guarded and facet in _FACETS_WITH_PARTNER_GUARD):
                continue
            for m in rx.finditer(f):
                if not _negated(f, m.start()):
                    add(facet, code, label, seg, m)
                    break
        agreement = AGREEMENT_WORDS.search(f)
        for code, label, rx in _BRANDS:
            for m in rx.finditer(f):
                if _negated(f, m.start()):
                    continue
                add("brand", code, label, seg, m)
                if agreement and not _negated(f, agreement.start()):
                    add("approval", f"brand_agreement:{code}", f"Agree / partenaire {label} (indique sur le site)", seg, m)
                break
    return list(found.values())


def merge_pages(per_page: list[list[dict]]) -> list[dict]:
    """Items of several pages (home first): the first page that states a (facet, code) keeps it."""
    seen, out = set(), []
    for items in per_page:
        for it in items:
            k = (it["facet"], it["code"])
            if k not in seen:
                seen.add(k)
                out.append(it)
    return out

