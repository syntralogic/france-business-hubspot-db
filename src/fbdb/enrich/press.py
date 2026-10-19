"""Press and third-party web mentions of a company, by web search (client spec, section 12, part 3). PAID (Firecrawl credits).

Per company ONE search `"Nom" "Ville"` (commercial name, else legal name, without SARL / SAS...). Results that are the company's own
site (proven by SIREN), social networks, company directories, job boards and review / lead sites are dropped (they are not press);
a result whose title / snippet does not even write the company name is dropped too. For each remaining candidate (at most 5):

  confirmed   a proof that the page is about THIS company: siren_on_page, website_linked (the page links to the site proven by SIREN),
              phone_match / email_match (one we already hold, sourced or verified), address_match (street words + postal code + city).
              The proof is looked for in the search snippet first (free) and then on the page of the first 2 candidates that have none (1 scrape each).
  to_verify   the name is written but nothing proves it is this company (homonyms exist): stored, NEVER exported, never counted as press.

`mention_passage` is the line of the snippet (or page) that writes the name; nothing is summarised or interpreted, no date or topic is
guessed (the search result does not give a reliable publication date, so none is stored). No phone or email is ever sent to the provider.

Absence is never "no press": press_searches.status = confirmed / to_verify / not_found (search ran, no usable mention) / inaccessible
(provider refused the query, retried after --retry-days); no row = not searched yet. A row remembers the search, so nobody is paid twice
before --refresh-days. A later search never deletes an article; a `to_verify` article that a later search proves is upgraded
(proof stored), a `confirmed` one is never downgraded.

Cost per company, worst case: 1 search + 2 scrapes = 3 credits, inside FIRECRAWL_DAILY_CAP (shared); a company is not started unless 3
credits are left. Search / scrape credit cost and the content of search results are NOT verified live (sandbox without key): run
`--limit 5`, read the log, then label ~100 results by hand to measure the wrong-confirmation rate. Pages behind a paywall give no proof:
then the article stays `to_verify`.

Usage: python -m fbdb.enrich.press [--limit 5] [--refresh-days 180] [--retry-days 7]. Needs DATABASE_URL, FIRECRAWL_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import psycopg

from fbdb.common import usage
from fbdb.enrich.social_search import _fold, find_evidence, load_facts, search_name
from fbdb.enrich.website import DAILY_CAP, PROVIDER, FirecrawlClient, QuotaExhausted

log = logging.getLogger("fbdb.press")
SEARCH_LIMIT = 8
MAX_CANDIDATES = 5
MAX_SCRAPES = 2
MAX_CREDITS = 1 + MAX_SCRAPES
# hosts that are not press: social networks, company directories / registries, job boards, review and lead sites, maps
NOT_PRESS = (
    "facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "youtube.com", "twitter.com", "x.com", "pinterest.com", "wikipedia.org",
    "societe.com", "pappers.fr", "infogreffe.fr", "verif.com", "manageo.fr", "entreprises.lefigaro.fr", "annuaire-entreprises.data.gouv.fr",
    "data.gouv.fr", "pagesjaunes.fr", "kompass.com", "europages.fr", "europages.com", "cylex.fr", "gralon.net", "118712.fr", "118218.fr",
    "mappy.com", "l-annuaire.com", "lannuaire.service-public.fr", "societeinfo.com", "score3.fr", "bilansgratuits.fr", "fr.indeed.com",
    "indeed.com", "glassdoor.fr", "glassdoor.com", "welcometothejungle.com", "hellowork.com", "jobijoba.com", "houzz.fr", "travaux.com",
    "habitatpresto.com", "quotatis.fr", "ootravaux.fr", "malt.fr", "trustpilot.com", "tripadvisor.fr", "google.com", "google.fr", "bing.com",
    "ufc-que-choisir.org", "qualibat.com", "qualifelec.fr", "faire-travaux.gouv.fr", "france-renov.gouv.fr",
)


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h.removeprefix("www.").removeprefix("m.")


def _on(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _alnum(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", _fold(text)).strip() + " "


def build_query(name: str, city: Optional[str]) -> str:
    return f'"{name}" "{" ".join((city or "").split())}"'.strip()


def mention_line(text: str, name: str) -> Optional[str]:
    """The line / sentence of `text` that writes the company name (accent- and case-insensitive, whole words), else None."""
    needle = _alnum(name)
    if len(needle.strip()) < 3:
        return None
    for line in (text or "").splitlines():
        for part in re.split(r"(?<=[.!?])\s+", line):
            if needle in _alnum(part):
                return " ".join(part.split())[:240]
    return None


def candidates(hits: list[dict], name: str, own_domains, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Search hits that can be a mention: not a directory / social / job board / own site, one per URL, name written in the title or snippet."""
    seen, out = set(), []
    for h in hits or []:
        url = (h.get("url") or "").split("#")[0]
        host = _host(url)
        key = url.rstrip("/").lower()
        if not url.startswith(("http://", "https://")) or not host or key in seen or _on(host, NOT_PRESS) or _on(host, own_domains):
            continue
        title, desc = (h.get("title") or "").strip(), (h.get("description") or "").strip()
        passage = mention_line(f"{title}\n{desc}", name)
        if not passage:
            continue
        seen.add(key)
        out.append({"url": url, "publisher": host, "title": title[:300] or None, "passage": passage, "snippet": f"{title}\n{desc}"})
        if len(out) >= limit:
            break
    return out


def search_press(client, name: str, query: str, facts: dict, reserve=lambda: True) -> dict:
    """One search, then the proof on snippets (free) and on up to 2 article pages (1 scrape each). Pure of the database.

    Returns {"status", "articles": [{url, publisher, title, passage, status, evidence_type, proof}], "hits_seen", "cap_reached"};
    cap_reached with no article and status 'not_found' means the search itself did not run (nothing to record)."""
    out = {"status": "not_found", "articles": [], "hits_seen": 0, "cap_reached": False}
    if not reserve():
        out["cap_reached"] = True
        return out
    hits = client.search_hits(query, SEARCH_LIMIT)
    if hits is None:
        out["status"] = "inaccessible"
        return out
    out["hits_seen"] = len(hits)
    arts = []
    for c in candidates(hits, name, facts.get("domains") or ()):
        a = {"url": c["url"], "publisher": c["publisher"], "title": c["title"], "passage": c["passage"], "status": "to_verify",
             "evidence_type": None, "proof": None}
        ev = find_evidence(c["snippet"], [], facts)                       # 1) proof in the snippet: no credit
        if ev:
            a.update(status="confirmed", evidence_type=ev["type"], proof=ev["passage"])
        arts.append(a)
    scraped = 0
    for a in arts:                                                         # 2) proof on the page: 1 scrape each, at most MAX_SCRAPES
        if a["status"] == "confirmed" or scraped >= MAX_SCRAPES:
            continue
        if not reserve():
            out["cap_reached"] = True
            break
        scraped += 1
        page = client.scrape(a["url"])
        if page:
            ev = find_evidence(page["markdown"], page["links"], facts)
            if ev:
                a.update(status="confirmed", evidence_type=ev["type"], proof=ev["passage"])
    out["articles"] = arts
    out["status"] = ("confirmed" if any(a["status"] == "confirmed" for a in arts) else "to_verify") if arts else "not_found"
    return out


# ---------------------------------------------------------------- database
DUE_SQL = """
SELECT c.company_id, c.siren, c.legal_name, c.trading_name, a.city, a.postal_code, a.street
FROM companies c JOIN company_addresses a ON a.company_id = c.company_id AND a.is_head_office AND a.status = 'active'
LEFT JOIN press_searches s ON s.company_id = c.company_id
WHERE c.status = 'active' AND c.is_diffusible AND a.city IS NOT NULL
  AND (s.company_id IS NULL
       OR (s.status <> 'inaccessible' AND s.searched_at < now() - make_interval(days => %(days)s))
       OR (s.status = 'inaccessible' AND s.searched_at < now() - make_interval(days => %(retry)s)))
ORDER BY s.searched_at NULLS FIRST, c.company_id LIMIT %(limit)s
"""


def store(cur, company_id: int, query: str, res: dict, source_id: int) -> dict:
    """Write the articles and the search memory. An article already stored is never deleted nor downgraded: a to_verify one that is
    now proven is upgraded. Returns {"new": n, "upgraded": n}."""
    new = upgraded = 0
    for a in res["articles"]:
        cur.execute("SELECT status FROM company_press WHERE company_id = %s AND article_url = %s", (company_id, a["url"]))
        prev = cur.fetchone()
        if prev is None:
            cur.execute(
                """INSERT INTO company_press (company_id, article_url, publisher, title, mention_passage, status, evidence_type, proof, query_text, source_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (company_id, a["url"], a["publisher"], a["title"], a["passage"], a["status"], a["evidence_type"], a["proof"], query, source_id))
            new += 1
        elif prev[0] == "to_verify" and a["status"] == "confirmed":
            cur.execute("UPDATE company_press SET status='confirmed', evidence_type=%s, proof=%s, last_verified_at=now() "
                        "WHERE company_id=%s AND article_url=%s", (a["evidence_type"], a["proof"], company_id, a["url"]))
            upgraded += 1
        else:
            cur.execute("UPDATE company_press SET last_verified_at=now() WHERE company_id=%s AND article_url=%s", (company_id, a["url"]))
    # the company status never goes down because a later search found less: it is derived from everything stored
    status = res["status"]
    if status in ("confirmed", "to_verify", "not_found"):
        cur.execute("SELECT CASE WHEN bool_or(status='confirmed') THEN 'confirmed' WHEN count(*) > 0 THEN 'to_verify' END "
                    "FROM company_press WHERE company_id = %s", (company_id,))
        status = cur.fetchone()[0] or "not_found"
    cur.execute(
        """INSERT INTO press_searches (company_id, searched_at, query_text, status, hits_seen, articles) VALUES (%s, now(), %s, %s, %s, %s)
           ON CONFLICT (company_id) DO UPDATE SET searched_at = now(), query_text = EXCLUDED.query_text, status = EXCLUDED.status,
             hits_seen = EXCLUDED.hits_seen, articles = EXCLUDED.articles""",
        (company_id, query, status, res["hits_seen"], len(res["articles"])))
    return {"new": new, "upgraded": upgraded, "status": status}


def run(dsn: str, client=None, limit: int = 5, refresh_days: int = 180, retry_days: int = 7, cap: int = DAILY_CAP) -> dict:
    client = client or FirecrawlClient()
    stats = {"companies": 0, "confirmed": 0, "to_verify": 0, "not_found": 0, "inaccessible": 0, "articles_added": 0, "upgraded": 0,
             "errors": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code = 'press_search'").fetchone()[0]
        rows = conn.execute(DUE_SQL, {"days": refresh_days, "retry": retry_days, "limit": limit}).fetchall()
        conn.commit()
        for company_id, siren, legal_name, trading_name, city, postal_code, street in rows:
            if usage.used_today(conn, PROVIDER)[1] + MAX_CREDITS > cap:    # never start a company whose search could not be finished
                stats["cap_reached"] = True
                break
            stats["companies"] += 1
            name = search_name(legal_name, trading_name)
            query = build_query(name, city)
            facts = load_facts(conn, company_id, siren, street, postal_code, city)
            try:
                res = search_press(client, name, query, facts, reserve=lambda: usage.reserve(conn, PROVIDER, cap, 1))
            except QuotaExhausted as exc:
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                stats["errors"] += 1
                log.warning("press search failed for %s: %s", siren, exc)
                continue
            if res["cap_reached"] and not res["articles"] and res["status"] == "not_found":
                stats["cap_reached"] = True              # the search did not run: nothing recorded, retried tomorrow
                break
            with conn.cursor() as cur:
                out = store(cur, company_id, query, res, source_id)
            conn.commit()
            stats["articles_added"] += out["new"]
            stats["upgraded"] += out["upgraded"]
            stats[out["status"]] += 1
            log.info("%s: %s, %d article(s)", siren, out["status"], len(res["articles"]))
            if res["cap_reached"]:
                stats["cap_reached"] = True
                break
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="companies per run (paid: start with 5)")
    ap.add_argument("--refresh-days", type=int, default=180)
    ap.add_argument("--retry-days", type=int, default=7, help="delay before an 'inaccessible' search is retried")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days, retry_days=a.retry_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
