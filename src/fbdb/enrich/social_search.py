"""Social accounts by web search, when the company's own site does not link them (client spec, section 9). PAID (Firecrawl credits).

Order of truth: a link on the site PROVEN by SIREN is already stored by fbdb.enrich.website (source 'website'). This module only
covers the networks for which the company has no account yet. For each missing network it runs ONE Firecrawl search
`site:facebook.com "Nom" "Ville"` (same shape for instagram.com, tiktok.com, linkedin.com/company, youtube.com; the director's
LinkedIn is `site:linkedin.com/in "Prenom Nom" "Nom"`), keeps the first profile URLs of that network, and looks for a PROOF:

  siren_on_page   our SIREN is written on the profile page or in the search snippet
  website_linked  the profile page links to the domain of the company's site proven by SIREN
  phone_match     a phone written on the page / snippet is one we already hold for the company (sourced or verified)
  email_match     same with an email
  address_match   postal code + city + every significant word of the head-office street are on the page / snippet

With a proof the account is `confirmed` (stored in company_contacts, value_status 'sourced', proof = the passage, source
'social_search'; the director's LinkedIn goes to company_interlocutors.profile_url). Without proof it stays `to_verify` in
social_searches ONLY: it is never a contact and never exported, because a name + a town is not a proof. For the director's profile
the person's first and last name must also be on the page. No phone or email is ever sent to the provider (only the name and the town).

Absence is never "no account": social_searches.status is confirmed / to_verify / not_found (search ran, no profile) / inaccessible
(provider refused the query); "non recherche" is the absence of a row. A row also remembers the search, so nobody is paid twice
before --refresh-days (inaccessible rows are retried after --retry-days). A later search never silently replaces what is kept: a
different candidate is written to field_conflicts (a proven account replaces a candidate; the displaced URL is recorded as the conflict).

Cost per company, worst case: 5 company networks + up to 2 directors, each 1 search + 1 page scrape = 14 credits, all inside
FIRECRAWL_DAILY_CAP (shared with fbdb.enrich.website). Profile pages of Facebook / Instagram / LinkedIn are often behind a login
wall: then only the search snippet can prove something, and the rest stays `to_verify`. Search credit cost and snippet content are
NOT verified live: run `--limit 5` first and read the log, then label ~100 results by hand to measure the wrong-confirmation rate.

Usage: python -m fbdb.enrich.social_search [--limit 5] [--networks facebook,instagram,...] [--refresh-days 180] [--retry-days 7]
Needs DATABASE_URL, FIRECRAWL_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import unicodedata
from typing import Optional
from urllib.parse import parse_qs, urlparse

import psycopg

from fbdb.common import usage
from fbdb.common.provenance import record_conflict
from fbdb.enrich import site_extract
from fbdb.enrich.website import DAILY_CAP, PROVIDER, FirecrawlClient, QuotaExhausted, find_sirens, proof_sentence
from fbdb.transform.normalize import LEGAL_FORM_NOISE, normalize_address

log = logging.getLogger("fbdb.social_search")
SEARCH_LIMIT = 5
MAX_CANDIDATES = 3        # profile URLs examined per search (snippets are free; only the first one is scraped)
MAX_DIRECTORS = 2         # directors searched per company
COMPANY_NETWORKS = ("facebook", "instagram", "tiktok", "linkedin_company", "youtube")
NETWORKS = COMPANY_NETWORKS + ("linkedin_director",)
SITE_OPERATOR = {"facebook": "facebook.com", "instagram": "instagram.com", "tiktok": "tiktok.com",
                 "linkedin_company": "linkedin.com/company", "linkedin_director": "linkedin.com/in", "youtube": "youtube.com"}

_FB_SKIP = {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "login", "login.php", "help", "policies", "tr", "l.php",
            "posts", "photos", "photo", "photo.php", "videos", "video", "watch", "reel", "reels", "stories", "story.php", "permalink.php",
            "groups", "events", "people", "public", "marketplace", "hashtag", "gaming", "ads", "business", "privacy", "media", "search",
            "pg", "profile", "home.php", "notes", "places"}
_IG_SKIP = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "directory", "about", "legal", "web", "developer", "challenge",
            "emailsignup", "direct", "locations", "tags"}


def search_name(legal_name: str, trading_name: Optional[str]) -> str:
    """Commercial name (else legal name) without the legal-form word (SARL, SAS...): pages are titled by the commercial name."""
    name = " ".join((trading_name or legal_name or "").split())
    kept = [t for t in name.split() if t.upper().strip(".,") not in LEGAL_FORM_NOISE]
    return " ".join(kept) or name


def build_query(network: str, name: str, city: Optional[str] = None, person: Optional[tuple[str, str]] = None) -> str:
    site = f"site:{SITE_OPERATOR[network]}"
    if network == "linkedin_director":
        first, last = person or ("", "")
        return f'{site} "{" ".join(f"{first} {last}".split())}" "{name}"'
    return f'{site} "{name}" "{" ".join((city or "").split())}"'.strip()


def _host_path(url: str) -> tuple[str, list[str], str]:
    try:
        p = urlparse(url)
    except ValueError:
        return "", [], ""
    host = (p.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "web.", "fr-fr.", "fr."):
        host = host.removeprefix(prefix)
    return host, [s for s in p.path.split("/") if s], p.query


def profile_url(network: str, url: str) -> Optional[str]:
    """Canonical profile URL when `url` is the PROFILE of that network (not a post, a share link, a video or a hashtag), else None."""
    host, segs, query = _host_path(url)
    if not segs:
        return None
    first = segs[0]
    low = first.lower()
    if network == "facebook":
        if host not in ("facebook.com", "fb.com"):
            return None
        if low == "profile.php":
            ids = parse_qs(query).get("id")
            return f"https://www.facebook.com/profile.php?id={ids[0]}" if ids and ids[0].isdigit() else None
        if low == "pages" and len(segs) >= 3:
            return "https://www.facebook.com/pages/" + "/".join(segs[1:3])
        return None if low in _FB_SKIP or low == "pages" else f"https://www.facebook.com/{first}"
    if network == "instagram":
        return f"https://www.instagram.com/{first}" if host == "instagram.com" and low not in _IG_SKIP else None
    if network == "tiktok":
        return f"https://www.tiktok.com/{first}" if host == "tiktok.com" and first.startswith("@") and len(first) > 1 else None
    if network == "linkedin_company":
        if host.endswith("linkedin.com") and low in ("company", "school") and len(segs) >= 2:
            return f"https://www.linkedin.com/{low}/{segs[1]}"
        return None
    if network == "linkedin_director":
        return f"https://www.linkedin.com/in/{segs[1]}" if host.endswith("linkedin.com") and low == "in" and len(segs) >= 2 else None
    if network == "youtube":
        if host not in ("youtube.com", "youtu.be"):
            return None
        if first.startswith("@") and len(first) > 1:
            return f"https://www.youtube.com/{first}"
        return f"https://www.youtube.com/{low}/{segs[1]}" if low in ("channel", "c", "user") and len(segs) >= 2 else None
    raise ValueError(f"unknown network {network}")


def candidates(network: str, hits: list[dict], limit: int = MAX_CANDIDATES) -> list[tuple[str, dict]]:
    """[(canonical profile URL, hit)] in search order, one per distinct profile."""
    seen, out = set(), []
    for h in hits or []:
        u = profile_url(network, h.get("url") or "")
        if u and u.lower() not in seen:
            seen.add(u.lower())
            out.append((u, h))
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------- proof
def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


_STREET_STOP = {"RUE", "AVENUE", "BOULEVARD", "CHEMIN", "IMPASSE", "PLACE", "ROUTE", "ALLEE", "QUAI", "COURS", "PASSAGE", "ZONE",
                "ARTISANALE", "INDUSTRIELLE", "DE", "DU", "DES", "LA", "LE", "LES", "ET", "AU", "AUX", "SUR", "SOUS", "BIS", "TER"}


def street_words(street: Optional[str]) -> list[str]:
    """Significant words of a street name, lowercase and without accents: no number, no street type, no article."""
    return [t.lower() for t in normalize_address(street, None, None).split()
            if t not in _STREET_STOP and len(t) >= 3 and not t.isdigit()]


def _line_with(text: str, needle: str) -> str:
    for line in (text or "").splitlines():
        if needle in _fold(line):
            return " ".join(line.split())[:240]
    return " ".join((text or "").split())[:240]


def _domain_linked(domain: str, links: list[str]) -> Optional[str]:
    pat = re.compile(rf"(?<![a-z0-9\-]){re.escape(domain)}(?![a-z0-9\-])(?!\.[a-z0-9])")
    for link in links or []:
        if pat.search(link.lower()):
            return link
    return None


def find_evidence(text: str, links: list[str], facts: dict, person: Optional[tuple[str, str]] = None) -> Optional[dict]:
    """The strongest proof that this page / snippet belongs to the company, or None. Pure.

    facts = {"siren", "phones": set(E.164), "emails": set, "domains": set (sites proven by SIREN), "postal_code", "city", "street"}.
    For a person's profile (`person` = (first, last)) both names must be on the page first. Returns {"type", "passage"}.
    """
    text = text or ""
    if person:
        f = _fold(text)
        if not all(re.search(rf"(?<![a-z]){re.escape(_fold(n))}(?![a-z])", f) for n in person if n):
            return None
    if facts.get("siren") and facts["siren"] in find_sirens(text):
        return {"type": "siren_on_page", "passage": proof_sentence(text, facts["siren"])}
    for domain in sorted(facts.get("domains") or ()):
        link = _domain_linked(domain, links)
        if link:
            return {"type": "website_linked", "passage": f"profile links to {link}"}
    for key, fn, kind in (("phones", site_extract.extract_phones, "phone_match"), ("emails", site_extract.extract_emails, "email_match")):
        known = facts.get(key) or set()
        for item in fn(text, links):
            if item["normalized"] in known:
                return {"type": kind, "passage": item["context"] or item["value"]}
    words, cp, city = street_words(facts.get("street")), (facts.get("postal_code") or "").strip(), _fold(facts.get("city") or "").strip()
    if words and cp and city:
        f = _fold(text)
        city_words = re.sub(r"\b\d+(er|eme|e)\b", "", city).split()
        if (re.search(rf"(?<!\d){re.escape(cp)}(?!\d)", f) and all(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", f) for w in words)
                and all(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", f) for w in city_words)):
            return {"type": "address_match", "passage": _line_with(text, cp)}
    return None


# ---------------------------------------------------------------- one search (pure of the database)
def search_network(client, network: str, query: str, facts: dict, reserve=lambda: True,
                   person: Optional[tuple[str, str]] = None) -> dict:
    """Search, then look for a proof on the snippets (free) and on the first candidate's page (1 scrape).

    Returns {"status", "candidate_url", "evidence_type", "proof", "proof_url", "cap_reached"}; cap_reached with status
    'not_found' and no candidate means the search itself did not run (nothing to record).
    """
    out = {"status": "not_found", "candidate_url": None, "evidence_type": None, "proof": None, "proof_url": None, "cap_reached": False}
    if not reserve():
        out["cap_reached"] = True
        return out
    hits = client.search_hits(query, SEARCH_LIMIT)
    if hits is None:
        out["status"] = "inaccessible"
        return out
    found = candidates(network, hits)
    if not found:
        return out
    for url, hit in found:                                     # 1) proof inside a search snippet: no extra credit
        ev = find_evidence(f"{hit.get('title') or ''}\n{hit.get('description') or ''}", [], facts, person)
        if ev:
            out.update(status="confirmed", candidate_url=url, evidence_type=ev["type"], proof=ev["passage"], proof_url=url)
            return out
    first_url, first_hit = found[0]                            # 2) proof on the first candidate's page: 1 scrape
    snippet = " - ".join(x for x in ((first_hit.get("title") or "").strip(), (first_hit.get("description") or "").strip()) if x)[:300]
    out.update(status="to_verify", candidate_url=first_url, proof=f"search result, no proof on it: {snippet}" if snippet else None,
               proof_url=first_url)
    if not reserve():
        out["cap_reached"] = True
        return out
    page = client.scrape(first_url)
    if page:
        ev = find_evidence(page["markdown"], page["links"], facts, person)
        if ev:
            out.update(status="confirmed", evidence_type=ev["type"], proof=ev["passage"])
    elif out["proof"]:
        out["proof"] += " | profile page not readable"
    return out


# ---------------------------------------------------------------- database
DUE_SQL = """
WITH base AS NOT MATERIALIZED (
  SELECT c.company_id, c.siren, c.legal_name, c.trading_name, a.city, a.postal_code, a.street
  FROM companies c JOIN company_addresses a ON a.company_id = c.company_id AND a.is_head_office AND a.status = 'active' AND a.is_diffusible
  WHERE c.status = 'active' AND c.is_diffusible AND a.city IS NOT NULL
), tasks AS (
  SELECT b.company_id, n.network, NULL::bigint AS interlocutor_id
  FROM base b CROSS JOIN unnest(%(networks)s::text[]) AS n(network)
  WHERE n.network <> 'linkedin_director'
    AND NOT EXISTS (SELECT 1 FROM company_contacts k WHERE k.company_id = b.company_id AND k.value_status <> 'unavailable' AND (
          (n.network = 'linkedin_company' AND k.contact_type = 'linkedin' AND k.value ~* '/(company|school)/')
       OR (n.network <> 'linkedin_company' AND k.contact_type = 'social' AND k.platform = n.network)))
    AND NOT EXISTS (SELECT 1 FROM social_searches s WHERE s.company_id = b.company_id AND s.network = n.network
          AND s.interlocutor_id IS NULL
          AND ((s.status <> 'inaccessible' AND s.searched_at >= now() - make_interval(days => %(days)s))
            OR (s.status = 'inaccessible' AND s.searched_at >= now() - make_interval(days => %(retry)s))))
  UNION ALL
  SELECT b.company_id, 'linkedin_director', i.interlocutor_id
  FROM base b JOIN LATERAL (
         SELECT interlocutor_id FROM company_interlocutors i
         WHERE i.company_id = b.company_id AND i.is_active AND i.function_category = 'director'
           AND i.first_name IS NOT NULL AND i.profile_url IS NULL ORDER BY i.interlocutor_id LIMIT %(max_directors)s) i ON TRUE
  WHERE 'linkedin_director' = ANY(%(networks)s::text[])
    AND NOT EXISTS (SELECT 1 FROM social_searches s WHERE s.interlocutor_id = i.interlocutor_id AND s.network = 'linkedin_director'
          AND ((s.status <> 'inaccessible' AND s.searched_at >= now() - make_interval(days => %(days)s))
            OR (s.status = 'inaccessible' AND s.searched_at >= now() - make_interval(days => %(retry)s))))
), picked AS (
  SELECT t.company_id FROM tasks t
  LEFT JOIN (SELECT company_id, max(searched_at) AS m FROM social_searches GROUP BY company_id) s ON s.company_id = t.company_id
  GROUP BY t.company_id, s.m ORDER BY s.m NULLS FIRST, t.company_id LIMIT %(limit)s
)
SELECT b.company_id, b.siren, b.legal_name, b.trading_name, b.city, b.postal_code, b.street,
       t.network, t.interlocutor_id, i.first_name, i.last_name
FROM tasks t JOIN picked p ON p.company_id = t.company_id JOIN base b ON b.company_id = t.company_id
LEFT JOIN company_interlocutors i ON i.interlocutor_id = t.interlocutor_id
ORDER BY b.company_id, t.network, t.interlocutor_id
"""


def load_facts(conn, company_id: int, siren: str, street, postal_code, city) -> dict:
    """What we already know about the company and can compare with a profile. Nothing leaves the database."""
    facts = {"siren": siren, "phones": set(), "emails": set(), "domains": set(), "street": street, "postal_code": postal_code, "city": city}
    for ctype, value, proven in conn.execute(
            """SELECT contact_type, value_normalized, match_proof IS NOT NULL FROM company_contacts
               WHERE company_id = %s AND value_status IN ('verified','sourced') AND contact_type IN ('phone','email','website')""",
            (company_id,)).fetchall():
        if ctype == "phone":
            facts["phones"].add(value)
        elif ctype == "email":
            facts["emails"].add(value)
        elif proven:                                  # only a site proven by SIREN counts as "the company's site"
            facts["domains"].add(value)
    return facts


def store_result(cur, company_id: int, network: str, interlocutor_id: Optional[int], query: str, res: dict, source_id: int) -> str:
    """Write the search memory row and, for a proven account, the contact. Never silently overwrites: returns what happened
    ('written', 'kept' = an earlier candidate/account was kept, 'conflict' = a different URL was recorded in field_conflicts)."""
    cur.execute("SELECT status, candidate_url FROM social_searches WHERE company_id = %s AND network = %s "
                "AND coalesce(interlocutor_id, 0) = coalesce(%s, 0)", (company_id, network, interlocutor_id))
    prev = cur.fetchone()
    field = f"social:{network}" + (f":{interlocutor_id}" if interlocutor_id else "")
    new_url, status = res["candidate_url"], res["status"]
    outcome = "written"
    if prev and prev[0] in ("confirmed", "to_verify") and prev[1]:
        old_url = prev[1]
        if status in ("not_found", "inaccessible"):
            # a later search that finds nothing does not erase a candidate: only the date moves
            cur.execute("UPDATE social_searches SET searched_at = now(), last_verified_at = now(), query_text = %s "
                        "WHERE company_id = %s AND network = %s AND coalesce(interlocutor_id, 0) = coalesce(%s, 0)",
                        (query, company_id, network, interlocutor_id))
            return "kept"
        if new_url.lower() != old_url.lower():
            if prev[0] == "confirmed" or status == "to_verify":
                record_conflict(cur.connection, company_id, field, old_url, source_id, new_url, source_id)
                cur.execute("UPDATE social_searches SET searched_at = now(), last_verified_at = now() WHERE company_id = %s "
                            "AND network = %s AND coalesce(interlocutor_id, 0) = coalesce(%s, 0)",
                            (company_id, network, interlocutor_id))
                return "conflict"
            record_conflict(cur.connection, company_id, field, new_url, source_id, old_url, source_id)   # proven replaces a candidate
            outcome = "conflict"
    cur.execute(
        """INSERT INTO social_searches (company_id, network, interlocutor_id, searched_at, query_text, status, candidate_url,
                                        evidence_type, proof, proof_url, last_verified_at)
           VALUES (%s,%s,%s, now(), %s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (company_id, network, (coalesce(interlocutor_id, 0))) DO UPDATE SET
             searched_at = now(), query_text = EXCLUDED.query_text, status = EXCLUDED.status, candidate_url = EXCLUDED.candidate_url,
             evidence_type = EXCLUDED.evidence_type, proof = EXCLUDED.proof, proof_url = EXCLUDED.proof_url, last_verified_at = now()""",
        (company_id, network, interlocutor_id, query, status, new_url, res["evidence_type"], res["proof"], res["proof_url"]))
    if status == "confirmed":
        mirror_confirmed(cur, company_id, network, interlocutor_id, res, source_id)
    return outcome


def mirror_confirmed(cur, company_id: int, network: str, interlocutor_id: Optional[int], res: dict, source_id: int) -> None:
    proof = f"{res['evidence_type']}: {res['proof']}"
    if network == "linkedin_director":
        cur.execute("UPDATE company_interlocutors SET profile_url = %s, match_proof = concat_ws(' | ', match_proof, %s::text), "
                    "last_verified_at = now() WHERE interlocutor_id = %s AND profile_url IS NULL",
                    (res["candidate_url"], "linkedin profile found by search, " + proof, interlocutor_id))
        return
    ctype = "linkedin" if network == "linkedin_company" else "social"
    cur.execute(
        """INSERT INTO company_contacts (company_id, contact_type, platform, value, value_normalized, value_status, source_id,
                                         match_proof, match_url, last_verified_at)
           VALUES (%s,%s,%s,%s,%s,'sourced',%s,%s,%s, now()) ON CONFLICT (company_id, contact_type, value_normalized) DO NOTHING""",
        (company_id, ctype, network if ctype == "social" else None, res["candidate_url"], res["candidate_url"].lower().rstrip("/"),
         source_id, "account found by web search, " + proof, res["proof_url"]))


def run(dsn: str, client=None, limit: int = 5, networks: tuple[str, ...] = NETWORKS, refresh_days: int = 180, retry_days: int = 7,
        cap: int = DAILY_CAP) -> dict:
    bad = [n for n in networks if n not in NETWORKS]
    if bad:
        raise ValueError(f"unknown networks {bad}: use {list(NETWORKS)}")
    client = client or FirecrawlClient()
    stats = {"companies": 0, "searches": 0, "confirmed": 0, "to_verify": 0, "not_found": 0, "inaccessible": 0, "conflicts": 0,
             "errors": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code = 'social_search'").fetchone()[0]
        rows = conn.execute(DUE_SQL, {"networks": list(networks), "days": refresh_days, "retry": retry_days, "limit": limit,
                                      "max_directors": MAX_DIRECTORS}).fetchall()
        conn.commit()
        seen_companies, facts_by_company = set(), {}
        for (company_id, siren, legal_name, trading_name, city, postal_code, street, network, interlocutor_id, first, last) in rows:
            if company_id not in seen_companies:
                seen_companies.add(company_id)
                stats["companies"] += 1
                facts_by_company[company_id] = load_facts(conn, company_id, siren, street, postal_code, city)
            person = (first, last) if network == "linkedin_director" else None
            query = build_query(network, search_name(legal_name, trading_name), city, person)
            try:
                res = search_network(client, network, query, facts_by_company[company_id],
                                     reserve=lambda: usage.reserve(conn, PROVIDER, cap, 1), person=person)
            except QuotaExhausted as exc:
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                stats["errors"] += 1
                log.warning("social search failed for %s/%s: %s", siren, network, exc)
                continue
            if res["cap_reached"] and res["candidate_url"] is None:
                stats["cap_reached"] = True              # the search did not run: nothing recorded, retried tomorrow
                break
            with conn.cursor() as cur:
                if store_result(cur, company_id, network, interlocutor_id, query, res, source_id) == "conflict":
                    stats["conflicts"] += 1
            conn.commit()
            stats["searches"] += 1
            stats[res["status"]] += 1
            log.info("%s %s: %s %s", siren, network, res["status"], res["candidate_url"] or "")
            if res["cap_reached"]:
                stats["cap_reached"] = True
                break
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="companies per run (paid: start with 5)")
    ap.add_argument("--networks", default=",".join(NETWORKS), help="comma list, default all: " + ",".join(NETWORKS))
    ap.add_argument("--refresh-days", type=int, default=180)
    ap.add_argument("--retry-days", type=int, default=7, help="delay before an 'inaccessible' search is retried")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, networks=tuple(n.strip() for n in a.networks.split(",") if n.strip()),
            refresh_days=a.refresh_days, retry_days=a.retry_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
