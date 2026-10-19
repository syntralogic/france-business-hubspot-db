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
from fbdb.enrich import site_extract
from fbdb.enrich.interlocutors import attach_person, link_contact
from fbdb.match.resolve import contact_conflict
from fbdb.transform.normalize import is_valid_siren, is_valid_siret

log = logging.getLogger("fbdb.website")
BASE = os.environ.get("FIRECRAWL_BASE", "https://api.firecrawl.dev/v1/scrape")
SEARCH_BASE = os.environ.get("FIRECRAWL_SEARCH_BASE", "https://api.firecrawl.dev/v1/search")
DAILY_CAP = int(os.environ.get("FIRECRAWL_DAILY_CAP", "100"))
PROVIDER = "firecrawl"
MAX_PAGES = 4          # home + legal page(s) until the SIREN proof, then at most the contact/team pages
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


def legal_pages(base_url: str, links: list[str], limit: int = 2) -> list[str]:
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
    def search_hits(self, query: str, limit: int = 5) -> Optional[list[dict]]:
        """Hits of a web search, best first: [{"url", "title", "description"}] ([] when nothing), or None when the provider
        REFUSED the query (4xx other than key/credit errors, or success=false): "no result" and "could not search" differ.
        Firecrawl v1 search: POST /v1/search, body {"query", "limit", "lang", "country"},
        answer {"success", "data": [{"url", "title", "description"}]}. NOT verified against a live key (sandbox blocked)."""
        r = self._http.post(SEARCH_BASE, headers={"Authorization": f"Bearer {self._key}"},
                            json={"query": query, "limit": limit, "lang": "fr", "country": "fr"})
        if r.status_code in (401, 402, 403):
            raise QuotaExhausted(f"Firecrawl HTTP {r.status_code}: invalid key or no credits left")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            return None
        body = r.json()
        if not body.get("success", True):
            return None
        return [{"url": d["url"], "title": d.get("title") or "", "description": d.get("description") or ""}
                for d in (body.get("data") or []) if isinstance(d, dict) and d.get("url")]

    def search(self, query: str, limit: int = 5) -> list[str]:
        """Result URLs of a web search, best first ([] when nothing or when the query was refused)."""
        return [h["url"] for h in (self.search_hits(query, limit) or [])]


def check_site(client, siren: str, site_url: str, reserve=lambda: True) -> dict:
    """Scrape the home page, then the likely legal pages until the SIREN proof; once proven, also the contact/team pages.

    Pure of the database. `reserve()` is called before every scrape and returns False when the daily cap is reached.
    Returns {"proven", "proof", "proof_url", "socials", "pages", "cap_reached", "phones", "emails", "people"}; phones, emails
    and people are read from every page scraped, and ONLY when the site is proven (each item keeps the page URL it came from).
    """
    res = {"proven": False, "proof": None, "proof_url": None, "socials": {}, "pages": 0, "cap_reached": False,
           "phones": [], "emails": [], "people": []}
    queue, seen, links_all, scraped = [site_url], set(), [], []
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
        scraped.append((url, page))
        if not res["proven"]:
            if siren in find_sirens(page["markdown"]):
                res.update(proven=True, proof=proof_sentence(page["markdown"], siren), proof_url=url)
                queue = [u for u in site_extract.contact_pages(site_url, links_all) if u not in seen]
            elif url == site_url:
                queue += legal_pages(site_url, page["links"])
    if res["proven"]:
        res["texts"] = [(url, page["markdown"]) for url, page in scraped]      # for --save-pages (people precision sample)
        res["socials"] = social_links(links_all)
        for url, page in scraped:
            for key, fn in (("phones", site_extract.extract_phones), ("emails", site_extract.extract_emails)):
                known = {i["normalized"] for i in res[key]}
                res[key] += [{**i, "url": url} for i in fn(page["markdown"], page["links"]) if i["normalized"] not in known]
            for p in site_extract.extract_people(page["markdown"]):
                same = next((q for q in res["people"] if (q["first_name"].lower(), q["last_name"].lower())
                             == (p["first_name"].lower(), p["last_name"].lower())), None)
                if same is None:
                    res["people"].append({**p, "url": url})
                    continue
                # same person on another page: keep every contact the pages put next to them, and a function if we had none
                same["phones"] += [v for v in p["phones"] if v not in same["phones"]]
                same["emails"] += [v for v in p["emails"] if v not in same["emails"]]
                if not same.get("function") and p.get("function"):
                    same.update(function=p["function"], line=p["line"], rule=p["rule"], url=url)
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


def _put_contact(cur, company_id: int, ctype: str, item: dict, source_id: int, site_proof: str):
    """Insert a phone/email read on the proven site. Returns (contact_id, added). Existing values are never touched."""
    cur.execute("SELECT contact_id FROM company_contacts WHERE company_id = %s AND contact_type = %s AND value_normalized = %s",
                (company_id, ctype, item["normalized"]))
    row = cur.fetchone()
    if row:
        return row[0], False
    cur.execute("SELECT 1 FROM company_contacts WHERE company_id = %s AND contact_type = %s AND establishment_id IS NULL AND is_primary",
                (company_id, ctype))
    has_primary = cur.fetchone() is not None
    contact_conflict(cur, company_id, ctype, item["normalized"], source_id)
    proof = f"{ctype} written on {item['url']}: {item['context'][:120]} | {site_proof}"
    cur.execute(
        """INSERT INTO company_contacts (company_id, contact_type, value, value_normalized, is_primary, value_status, source_id,
                 match_proof, match_url, contact_use, last_verified_at)
           VALUES (%s,%s,%s,%s,%s,'sourced',%s,%s,%s,%s, now()) RETURNING contact_id""",
        (company_id, ctype, item["value"], item["normalized"], not has_primary, source_id, proof, item["url"], item.get("use")))
    return cur.fetchone()[0], True


def store_extracted(cur, company_id: int, result: dict, source_id: int) -> dict:
    """Phones, emails and people read on a PROVEN site. A phone/email is linked to a person only when the page puts it next to
    that person's name (evidence `published_with_name`); every other value stays 'company - person not identified'."""
    stats = {"phones": 0, "emails": 0, "people": 0, "person_links": 0}
    if not result.get("proven"):
        return stats
    site_proof = "website proven by SIREN: " + (result.get("proof") or "")
    for key, ctype in (("phones", "phone"), ("emails", "email")):
        for item in result.get(key) or []:
            stats[key] += _put_contact(cur, company_id, ctype, item, source_id, site_proof)[1]
    for p in result.get("people") or []:
        pid, new = attach_person(cur, company_id, p["last_name"], p["first_name"], p.get("function"),
                                 match_proof=f"{p['rule']}: {p['line']} | {site_proof}", source_url=p["url"])
        stats["people"] += new
        for ctype, values in (("phone", p["phones"]), ("email", p["emails"])):
            for value in values:
                try:
                    link_contact(cur, company_id, pid, ctype, value, "published_with_name")
                    stats["person_links"] += 1
                except ValueError as exc:      # value held under another person: leave it for a human
                    log.info("not linked %s to %s: %s", value, p["last_name"], exc)
    return stats


def save_pages(directory: str, siren: str, texts: list[tuple[str, str]]) -> int:
    """Write the pages read on a PROVEN site as `<siren>__NN.md` (first line: `<!-- source: URL -->`) so that people / contact extraction can
    be measured and re-run offline (fbdb.report.people_eval) without paying for the scrape again. Returns the number of files written."""
    os.makedirs(directory, exist_ok=True)
    for i, (url, markdown) in enumerate(texts, 1):
        with open(os.path.join(directory, f"{siren}__{i:02d}.md"), "w", encoding="utf-8") as fh:
            fh.write(f"<!-- source: {url} -->\n{markdown}")
    return len(texts)


def run(dsn: str, client=None, limit: int = 100, refresh_days: int = 90, cap: int = DAILY_CAP, save_pages_dir: Optional[str] = None) -> dict:
    client = client or FirecrawlClient()
    stats = {"read": 0, "proven": 0, "not_proven": 0, "socials_added": 0, "phones_added": 0, "emails_added": 0, "people_added": 0,
             "person_links": 0, "errors": 0, "cap_reached": False, "stopped_no_credits": False}
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
                ex = store_extracted(cur, company_id, res, source_id)
                stats["phones_added"] += ex["phones"]; stats["emails_added"] += ex["emails"]
                stats["people_added"] += ex["people"]; stats["person_links"] += ex["person_links"]
            if save_pages_dir and res["proven"]:
                save_pages(save_pages_dir, siren, res.get("texts") or [])
            stats["proven" if res["proven"] else "not_proven"] += 1
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--refresh-days", type=int, default=90)
    ap.add_argument("--save-pages", metavar="DIR", help="also write the pages read on proven sites to DIR (for fbdb.report.people_eval)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days, save_pages_dir=a.save_pages)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
