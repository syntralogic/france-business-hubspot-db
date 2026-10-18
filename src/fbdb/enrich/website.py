"""Official website proof and social links, from the company's own pages (Firecrawl scrape). PAID (Firecrawl credits).

A website is stored ONLY when the SIREN of the company is found (Luhn-valid and equal to ours) on the site, normally on the
"mentions legales" page. That sentence is the proof (company_contacts.match_proof / match_url) and the value is 'verified'.
No SIREN on the site -> nothing is stored: a name or a town is not a proof. Social links (Facebook, Instagram, TikTok, LinkedIn,
YouTube, X) are taken from the SAME proven site only, so each one has the same proof.

Candidates: existing 'website' contacts of the company (for example from the licensed file) that are not proven yet.
Discovery by web search lives in fbdb.enrich.website_discovery: it only proposes candidate URLs and every one still has to pass
this same SIREN proof (it is the biggest source of wrong matches, so measure it on a labelled sample before trusting it).

Firecrawl v1 scrape: POST https://api.firecrawl.dev/v1/scrape, Bearer key, body {"url", "formats": ["markdown", "links"]},
answer {"success", "data": {"markdown", "links"}}. NOT verified against a live key from the build sandbox (blocked): run `--limit 5`
first. Daily cap: FIRECRAWL_DAILY_CAP scrapes (default 100), atomic via fbdb.common.usage; up to 3 pages per site are scraped.

Usage: python -m fbdb.enrich.website [--limit 100]. Needs DATABASE_URL, FIRECRAWL_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
import psycopg
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fbdb.common import usage
from fbdb.transform.normalize import is_valid_siren, is_valid_siret

log = logging.getLogger("fbdb.website")
BASE = os.environ.get("FIRECRAWL_BASE", "https://api.firecrawl.dev/v1/scrape")
SEARCH_BASE = os.environ.get("FIRECRAWL_SEARCH_BASE", "https://api.firecrawl.dev/v1/search")
DAILY_CAP = int(os.environ.get("FIRECRAWL_DAILY_CAP", "100"))
PROVIDER = "firecrawl"
MAX_PAGES = 3
LEGAL_HINTS = ("mention", "legal", "cgv", "cgu", "a-propos", "apropos", "about", "contact", "societe", "qui-sommes")
_SEP = r"[ \u00a0\u202f.\-]?"
SIREN_RE = re.compile(rf"(?<!\d)(\d{{3}}){_SEP}(\d{{3}}){_SEP}(\d{{3}})(?:{_SEP}(\d{{5}}))?(?!\d)")
SOCIAL_HOSTS = {
    "facebook": ("facebook.com", "fb.com"), "instagram": ("instagram.com",), "tiktok": ("tiktok.com",),
    "linkedin": ("linkedin.com",), "youtube": ("youtube.com", "youtu.be"), "x": ("twitter.com", "x.com"),
}
# first path segments that are not a profile (share buttons, login, generic pages)
SOCIAL_SKIP = {"sharer", "sharer.php", "share", "intent", "login", "dialog", "plugins", "watch", "embed", "hashtag", "tr", "policies", "help"}


class RetryableError(Exception):
    pass


class QuotaExhausted(Exception):
    """Firecrawl 401/402/403: bad key or no credits left. Stop, do not retry."""


def find_sirens(text: str) -> set[str]:
    """Every Luhn-valid SIREN (9 digits, or the first 9 of a Luhn-valid 14-digit SIRET) written in `text`."""
    found = set()
    for m in SIREN_RE.finditer(text or ""):
        siren = m.group(1) + m.group(2) + m.group(3)
        if m.group(4):
            if is_valid_siret(siren + m.group(4)):
                found.add(siren)
        elif is_valid_siren(siren):
            found.add(siren)
    return found


def proof_sentence(text: str, siren: str, width: int = 60) -> Optional[str]:
    """A short excerpt around the SIREN, kept as the human-readable proof."""
    for m in SIREN_RE.finditer(text or ""):
        if m.group(1) + m.group(2) + m.group(3) == siren:
            a, b = max(0, m.start() - width), min(len(text), m.end() + width)
            return " ".join(text[a:b].split())
    return None


def social_links(links: list[str]) -> dict[str, str]:
    """{platform: url} one profile per platform (first found), share/login links ignored."""
    out: dict[str, str] = {}
    for url in links or []:
        try:
            p = urlparse(url)
        except ValueError:
            continue
        host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("fr-fr.")
        segs = [s for s in p.path.split("/") if s]
        if not segs:
            continue
        for platform, hosts in SOCIAL_HOSTS.items():
            if platform in out or host not in hosts:
                continue
            first = segs[0].lower()
            if first in SOCIAL_SKIP:
                continue
            if platform == "linkedin" and first not in ("company", "in", "school"):
                continue
            if platform == "youtube" and first not in ("channel", "c", "user") and not first.startswith("@"):
                continue
            out[platform] = url.split("?")[0].split("#")[0]
    return out


def legal_pages(base_url: str, links: list[str], limit: int = MAX_PAGES - 1) -> list[str]:
    """Same-site pages that are likely to carry the legal notice, best hints first."""
    host = (urlparse(base_url).hostname or "").removeprefix("www.")
    scored = []
    for link in links or []:
        full = urljoin(base_url, link)
        p = urlparse(full)
        if (p.hostname or "").removeprefix("www.") != host or full.rstrip("/") == base_url.rstrip("/"):
            continue
        path = p.path.lower()
        for rank, hint in enumerate(LEGAL_HINTS):
            if hint in path:
                scored.append((rank, full.split("#")[0]))
                break
    seen, out = set(), []
    for _, u in sorted(scored):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:limit]


class FirecrawlClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 90.0, transport=None):
        self._key = api_key or os.environ["FIRECRAWL_API_KEY"]
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(3), reraise=True)
    def scrape(self, url: str) -> Optional[dict]:
        """{"markdown": str, "links": [str]} or None when the page cannot be scraped (4xx on that URL)."""
        r = self._http.post(BASE, headers={"Authorization": f"Bearer {self._key}"},
                            json={"url": url, "formats": ["markdown", "links"]})
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Firecrawl HTTP {r.status_code}: invalid key or no credits left")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            return None
        body = r.json()
        data = body.get("data") or {}
        if not body.get("success", True) or not data:
            return None
        return {"markdown": data.get("markdown") or "", "links": data.get("links") or []}

    @retry(retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
           wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(3), reraise=True)
    def search(self, query: str, limit: int = 5) -> list[str]:
        """Result URLs of a web search, best first ([] when nothing). Firecrawl v1 search: POST /v1/search,
        body {"query", "limit", "lang", "country"}, answer {"success", "data": [{"url", "title", "description"}]}.
        NOT verified against a live key from the build sandbox (blocked)."""
        r = self._http.post(SEARCH_BASE, headers={"Authorization": f"Bearer {self._key}"},
                            json={"query": query, "limit": limit, "lang": "fr", "country": "fr"})
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Firecrawl HTTP {r.status_code}: invalid key or no credits left")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            return []
        body = r.json()
        if not body.get("success", True):
            return []
        return [d["url"] for d in (body.get("data") or []) if isinstance(d, dict) and d.get("url")]


def check_site(client, siren: str, site_url: str, reserve=lambda: True) -> dict:
    """Scrape the home page then the likely legal pages (stop at the first proof). Pure of the database.

    `reserve()` is called before every scrape and returns False when the daily cap is reached.
    Returns {"proven": bool, "proof": str|None, "proof_url": str|None, "socials": {...}, "pages": n, "cap_reached": bool}.
    """
    res = {"proven": False, "proof": None, "proof_url": None, "socials": {}, "pages": 0, "cap_reached": False}
    queue, seen, links_all = [site_url], set(), []
    while queue and res["pages"] < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not reserve():
            res["cap_reached"] = True
            break
        page = client.scrape(url)
        res["pages"] += 1
        if not page:
            continue
        links_all += page["links"]
        if siren in find_sirens(page["markdown"]):
            res.update(proven=True, proof=proof_sentence(page["markdown"], siren), proof_url=url)
            res["socials"] = social_links(links_all)
            return res
        if url == site_url:
            queue += legal_pages(site_url, page["links"])
    return res


SELECT_DUE = """
SELECT k.contact_id, k.company_id, c.siren, k.value
FROM company_contacts k JOIN companies c ON c.company_id = k.company_id
WHERE k.contact_type = 'website' AND k.match_proof IS NULL AND c.status = 'active' AND c.is_diffusible
  AND (k.checked_at IS NULL OR k.checked_at < now() - make_interval(days => %(days)s))
ORDER BY k.checked_at NULLS FIRST, k.contact_id LIMIT %(limit)s
"""


def _url(value: str) -> str:
    return value if value.startswith("http") else "https://" + value


def store(cur, contact_id: int, company_id: int, result: dict, source_id: int) -> int:
    """Mark the website verified with its proof and add the social profiles found on it. Returns social rows written."""
    cur.execute("UPDATE company_contacts SET checked_at = now() WHERE contact_id = %s", (contact_id,))
    if not result["proven"]:
        return 0
    cur.execute(
        """UPDATE company_contacts SET value_status='verified', match_proof=%s, match_url=%s, last_verified_at=now()
           WHERE contact_id=%s""", (result["proof"], result["proof_url"], contact_id))
    n = 0
    for platform, url in result["socials"].items():
        ctype = "linkedin" if platform == "linkedin" else "social"
        norm = url.lower().rstrip("/")
        cur.execute(
            """INSERT INTO company_contacts (company_id, contact_type, platform, value, value_normalized, value_status,
                 source_id, match_proof, match_url, last_verified_at)
               VALUES (%s,%s,%s,%s,%s,'sourced',%s,%s,%s,now()) ON CONFLICT (company_id, contact_type, value_normalized) DO NOTHING""",
            (company_id, ctype, platform if ctype == "social" else None, url, norm, source_id,
             "link found on website proven by SIREN: " + (result["proof"] or ""), result["proof_url"]))
        n += cur.rowcount
    return n


def run(dsn: str, client=None, limit: int = 100, refresh_days: int = 90, cap: int = DAILY_CAP) -> dict:
    client = client or FirecrawlClient()
    stats = {"read": 0, "proven": 0, "not_proven": 0, "socials_added": 0, "errors": 0, "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code='website'").fetchone()[0]
        for contact_id, company_id, siren, value in conn.execute(SELECT_DUE, {"days": refresh_days, "limit": limit}).fetchall():
            stats["read"] += 1
            try:
                res = check_site(client, siren, _url(value), reserve=lambda: usage.reserve(conn, PROVIDER, cap, 1))
            except QuotaExhausted as exc:
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                stats["errors"] += 1
                log.warning("website check failed for %s: %s", siren, exc)
                continue
            if res["cap_reached"] and not res["proven"]:
                stats["cap_reached"] = True
                break
            with conn.cursor() as cur:
                stats["socials_added"] += store(cur, contact_id, company_id, res, source_id)
            stats["proven" if res["proven"] else "not_proven"] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--refresh-days", type=int, default=90)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
