"""Website discovery by web search (Firecrawl search), for companies that have NO website contact yet. PAID.

Search only PROPOSES candidate URLs. A candidate is stored ONLY if the company's own SIREN is found on it (same proof rule as
fbdb.enrich.website.check_site), and the proof must sit on the home page or on a legal/contact/about page: a SIREN in a
"nos partenaires" or blog page is not a proof of ownership. Directories, registries and social networks are never candidates,
because they publish SIRENs of companies that are not theirs. Nothing is stored when nothing is proven.

website_searches keeps one row per company searched, with outcome 'proven' / 'not_proven' / 'no_results'. 'not_proven' means
"no candidate showed our SIREN", never "this company has no website". The company is searched again after --refresh-days.

Cost per company (worst case): 1 search + up to WEBSITE_SEARCH_MAX_CANDIDATES (default 2) x 3 page scrapes, all counted in the
daily cap FIRECRAWL_DAILY_CAP (shared with fbdb.enrich.website). Search credit cost is NOT verified: run `--limit 5` first.
The match rate and the wrong-match rate must be measured on a hand-labelled sample before this is run at scale.

Usage: python -m fbdb.enrich.website_discovery [--limit 50] [--refresh-days 180]. Needs DATABASE_URL, FIRECRAWL_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import psycopg

from fbdb.common import usage
from fbdb.enrich.website import DAILY_CAP, LEGAL_HINTS, PROVIDER, FirecrawlClient, QuotaExhausted, check_site, store
from fbdb.transform.normalize import normalize_domain

log = logging.getLogger("fbdb.website_discovery")
MAX_CANDIDATES = int(os.environ.get("WEBSITE_SEARCH_MAX_CANDIDATES", "2"))
SEARCH_LIMIT = 5
# Hosts that are never a company's own website (they list or repeat other companies' data, SIREN included).
BLOCKED_HOSTS = (
    "facebook.com", "instagram.com", "tiktok.com", "linkedin.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
    "pinterest.", "pagesjaunes.fr", "societe.com", "pappers.fr", "infogreffe.fr", "verif.com", "manageo.fr", "kompass.com",
    "annuaire-entreprises.data.gouv.fr", "data.gouv.fr", "entreprises.lefigaro.fr", "societeinfo.com", "score3.fr",
    "mappy.com", "google.", "bing.com", "yelp.", "tripadvisor.", "houzz.", "travaux.com", "habitatpresto.com", "leboncoin.fr",
    "wikipedia.org", "europages.", "indeed.", "glassdoor.", "welcometothejungle.com", "bodacc.fr", "journal-officiel.gouv.fr",
    "qualibat.com", "qualifelec.fr", "ademe.fr", "france-renov.gouv.fr", "artisans-du-batiment.", "starofservice.", "20minutes.fr",
)


def build_query(legal_name: str, trading_name: Optional[str], city: Optional[str]) -> str:
    """Quoted commercial name (else legal name) + town. The quotes keep the search on that exact name."""
    name = " ".join((trading_name or legal_name or "").split())
    return f'"{name}" {" ".join((city or "").split())}'.strip()


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def is_blocked(url: str) -> bool:
    """True for directories, registries and social networks. "name." entries (no TLD) block that brand on any TLD,
    matched on whole host labels so that e.g. mygoogle.fr is NOT blocked; other entries block the domain and its subdomains."""
    h = _host(url)
    if not h:
        return True
    labels = h.split(".")
    for b in BLOCKED_HOSTS:
        if b.endswith("."):
            if b[:-1] in labels[:-1]:
                return True
        elif h == b or h.endswith("." + b):
            return True
    return False


def candidate_sites(results: list[str], limit: int = MAX_CANDIDATES) -> list[str]:
    """Home-page URL of the first `limit` distinct hosts, in search order, without directories or social networks."""
    seen, out = set(), []
    for url in results or []:
        if not url.startswith(("http://", "https://")) or is_blocked(url):
            continue
        h = _host(url)
        if h in seen:
            continue
        seen.add(h)
        p = urlparse(url)
        out.append(f"{p.scheme}://{p.netloc}")
        if len(out) >= limit:
            break
    return out


def proof_is_acceptable(site_url: str, proof_url: Optional[str]) -> bool:
    """The SIREN must be on the home page or on a legal/contact/about page of the candidate."""
    if not proof_url:
        return False
    path = urlparse(proof_url).path.lower().rstrip("/")
    if proof_url.rstrip("/") == site_url.rstrip("/") or path == "":
        return True
    return any(h in path for h in LEGAL_HINTS)


def discover(client, siren: str, query: str, reserve=lambda: True, max_candidates: int = MAX_CANDIDATES) -> dict:
    """Search, then check each candidate. Pure of the database.

    Returns {"outcome": "proven"|"not_proven"|"no_results", "site_url", "result", "checked": n, "cap_reached": bool}.
    """
    out = {"outcome": "no_results", "site_url": None, "result": None, "checked": 0, "cap_reached": False}
    if not reserve():
        out["cap_reached"] = True
        return out
    sites = candidate_sites(client.search(query, SEARCH_LIMIT), max_candidates)
    if not sites:
        return out
    out["outcome"] = "not_proven"
    for site in sites:
        res = check_site(client, siren, site, reserve=reserve)
        out["checked"] += 1
        if res["cap_reached"] and not res["proven"]:
            out["cap_reached"] = True
            break
        if res["proven"] and proof_is_acceptable(site, res["proof_url"]):
            out.update(outcome="proven", site_url=site, result=res)
            break
    return out


SELECT_DUE = """
SELECT c.company_id, c.siren, c.legal_name, c.trading_name, a.city
FROM companies c
JOIN company_addresses a ON a.company_id = c.company_id AND a.is_head_office AND a.status = 'active'
LEFT JOIN website_searches s ON s.company_id = c.company_id
WHERE c.status = 'active' AND c.is_diffusible AND a.city IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM company_contacts k WHERE k.company_id = c.company_id AND k.contact_type = 'website')
  AND (s.searched_at IS NULL OR s.searched_at < now() - make_interval(days => %(days)s))
ORDER BY s.searched_at NULLS FIRST, c.company_id LIMIT %(limit)s
"""


def store_discovery(cur, company_id: int, query: str, found: dict, source_id: int) -> int:
    """Record the search; when proven, add the website (verified, with proof) and its social links. Returns socials added."""
    n = 0
    if found["outcome"] == "proven":
        cur.execute(
            """INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status, source_id,
                                             last_verified_at)
               VALUES (%s,'website',%s,%s,TRUE,'sourced',%s, now()) RETURNING contact_id""",
            (company_id, found["site_url"], normalize_domain(found["site_url"]) or _host(found["site_url"]), source_id))
        n = store(cur, cur.fetchone()[0], company_id, found["result"], source_id)
    cur.execute(
        """INSERT INTO website_searches (company_id, searched_at, query_text, candidates_checked, outcome)
           VALUES (%s, now(), %s, %s, %s)
           ON CONFLICT (company_id) DO UPDATE SET searched_at = now(), query_text = EXCLUDED.query_text,
             candidates_checked = EXCLUDED.candidates_checked, outcome = EXCLUDED.outcome""",
        (company_id, query, found["checked"], found["outcome"]))
    return n


def run(dsn: str, client=None, limit: int = 50, refresh_days: int = 180, cap: int = DAILY_CAP) -> dict:
    client = client or FirecrawlClient()
    stats = {"read": 0, "proven": 0, "not_proven": 0, "no_results": 0, "socials_added": 0, "errors": 0,
             "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code='website'").fetchone()[0]
        for company_id, siren, legal_name, trading_name, city in conn.execute(
                SELECT_DUE, {"days": refresh_days, "limit": limit}).fetchall():
            stats["read"] += 1
            query = build_query(legal_name, trading_name, city)
            try:
                found = discover(client, siren, query, reserve=lambda: usage.reserve(conn, PROVIDER, cap, 1))
            except QuotaExhausted as exc:
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                stats["errors"] += 1
                log.warning("website discovery failed for %s: %s", siren, exc)
                continue
            if found["cap_reached"] and found["outcome"] != "proven":
                stats["cap_reached"] = True   # not recorded: the company is retried tomorrow
                break
            with conn.cursor() as cur:
                stats["socials_added"] += store_discovery(cur, company_id, query, found, source_id)
            stats[found["outcome"]] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--refresh-days", type=int, default=180)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
