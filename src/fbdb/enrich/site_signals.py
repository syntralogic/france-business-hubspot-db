"""Realisations, partners and recruitment read from the pages of a company's OWN site proven by SIREN (client spec, section 12, part 2).
PAID (Firecrawl credits).

Same discipline as fbdb.enrich.site_activity: only a website contact that carries a SIREN proof (company_contacts.match_proof) is read.
Per company: the home page + up to 3 same-site pages chosen among realisations / chantiers / references, partners / clients / marques,
and recruitment / careers (one page of each kind first, then the next best). At most 4 scrapes per company, counted in
FIRECRAWL_DAILY_CAP (shared with website and site_activity); a company is not started unless 4 credits are left.
Note: this re-reads the home page that site_activity already read (the scrape is not cached): the two passes cost up to 8 credits
per company together. Merging them into a single read is a possible later saving.

What is stored (company_site_signals, kind = realisation | partner | hiring) is a CLAIM of the site, with its passage, page URL, date and
the SIREN proof of the site (rules and limits: fbdb.enrich.signal_rules). Photos and logo bars are never read. A partner name is never
matched to a company of our base. Absence is never concluded: signal_runs.outcome = extracted / nothing_found ("no signal found on the pages
read", NOT "never did a chantier" / "no partner" / "not recruiting") / inaccessible (home page unreadable, retried after --retry-days);
no row = not read yet. A claim is never deleted because a later read no longer finds it (only last_seen_at moves; a recruitment claim is
therefore "seen recruiting on <date>", never "recruiting now").

Scrape format and credit cost are NOT verified live (see fbdb.enrich.website). Run `--limit 5` first and read the log.

Usage: python -m fbdb.enrich.site_signals [--limit 5] [--refresh-days 90] [--retry-days 7]. Needs DATABASE_URL, FIRECRAWL_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
from urllib.parse import urljoin, urlparse

import psycopg

from fbdb.common import usage
from fbdb.enrich.signal_rules import HIRING_PAGE, PARTNER_PAGE, REALISATION_PAGE, extract_signals, merge_pages, page_kind
from fbdb.enrich.website import DAILY_CAP, PROVIDER, FirecrawlClient, QuotaExhausted

log = logging.getLogger("fbdb.site_signals")
MAX_EXTRA_PAGES = 3
MAX_PAGES = 1 + MAX_EXTRA_PAGES
SKIP_HINTS = ("blog", "actualit", "news", "article", "conseil", "astuce", "guide", "mention", "legal", "cgv", "cgu", "politique",
              "confidentialite", "cookie", "plan-du-site", "sitemap", "contact", "devis", "avis", "faq", "login", "panier", "boutique",
              "shop", "produit", "galerie", "gallery", "photo")
FILE_EXT = ("pdf", "jpg", "jpeg", "png", "gif", "webp", "zip", "svg", "mp4")


def signal_pages(base_url: str, links: list[str], limit: int = MAX_EXTRA_PAGES) -> list[str]:
    """Same-site pages likely to carry realisations / partners / recruitment. One page of each kind first (best hint), then the rest."""
    host = (urlparse(base_url).hostname or "").removeprefix("www.")
    by_kind: dict[str, list[tuple[int, int, str]]] = {"hiring": [], "realisation": [], "partner": []}
    hints = {"hiring": HIRING_PAGE, "realisation": REALISATION_PAGE, "partner": PARTNER_PAGE}
    seen = set()
    for link in links or []:
        full = urljoin(base_url, link).split("#")[0].split("?")[0]
        p = urlparse(full)
        if p.scheme not in ("http", "https") or (p.hostname or "").removeprefix("www.") != host:
            continue
        path = p.path.lower().rstrip("/")
        if not path or full.rstrip("/") == base_url.rstrip("/") or full in seen:
            continue
        if path.rsplit(".", 1)[-1] in FILE_EXT:
            continue
        kind = page_kind(full)
        if kind == "other" or (kind != "hiring" and any(h in path for h in SKIP_HINTS)):
            continue
        seen.add(full)
        rank = min(i for i, h in enumerate(hints[kind]) if h in path)
        by_kind[kind].append((rank, path.count("/"), full))
    for lst in by_kind.values():
        lst.sort()
    picked: list[str] = []
    for kind in ("realisation", "partner", "hiring"):          # one of each kind first
        if by_kind[kind] and len(picked) < limit:
            picked.append(by_kind[kind].pop(0)[2])
    rest = sorted(x for lst in by_kind.values() for x in lst)
    picked += [u for _, _, u in rest][: max(0, limit - len(picked))]
    return picked


def read_site(client, site_url: str, reserve=lambda: True) -> dict:
    """Scrape home then the chosen pages, extract. Pure of the database.
    Returns {"outcome", "items", "pages_read", "cap_reached"} (same contract as site_activity.read_site)."""
    out = {"outcome": "inaccessible", "items": [], "pages_read": 0, "cap_reached": False}
    if not reserve():
        out["cap_reached"] = True
        return out
    home = client.scrape(site_url)
    out["pages_read"] = 1 if home else 0
    if not home:
        return out
    per_page = [extract_signals(home["markdown"], site_url, "home")]
    for url in signal_pages(site_url, home["links"]):
        if not reserve():
            out["cap_reached"] = True
            break
        page = client.scrape(url)
        if not page:
            continue
        out["pages_read"] += 1
        per_page.append(extract_signals(page["markdown"], url, page_kind(url)))
    out["items"] = merge_pages(per_page)
    out["outcome"] = "extracted" if out["items"] else "nothing_found"
    return out


SELECT_DUE = """
SELECT w.company_id, w.siren, w.value, w.match_proof FROM (
  SELECT DISTINCT ON (k.company_id) k.company_id, c.siren, k.value, k.match_proof
  FROM company_contacts k JOIN companies c ON c.company_id = k.company_id
  WHERE k.contact_type = 'website' AND k.match_proof IS NOT NULL AND k.value_status IN ('verified','sourced')
    AND c.status = 'active' AND c.is_diffusible
  ORDER BY k.company_id, k.is_primary DESC, k.contact_id) w
LEFT JOIN signal_runs r ON r.company_id = w.company_id
WHERE r.company_id IS NULL
   OR (r.outcome <> 'inaccessible' AND r.searched_at < now() - make_interval(days => %(days)s))
   OR (r.outcome = 'inaccessible' AND r.searched_at < now() - make_interval(days => %(retry)s))
ORDER BY r.searched_at NULLS FIRST, w.company_id LIMIT %(limit)s
"""


def _url(value: str) -> str:
    return value if value.startswith("http") else "https://" + value


def store(cur, company_id: int, result: dict, site_proof: str, source_id: int) -> int:
    """Upsert the signals and remember the read. An existing signal keeps its first passage and URL (only last_seen_at moves);
    nothing is deleted. Returns the number of NEW signals."""
    new = 0
    for it in result["items"]:
        cur.execute(
            """INSERT INTO company_site_signals (company_id, kind, code, name, relation, year_mentioned, matched_text, passage, source_url, site_proof, source_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, kind, code) DO UPDATE SET last_seen_at = now()
               RETURNING (xmax = 0)""",
            (company_id, it["kind"], it["code"], it["name"], it["relation"], it["year_mentioned"], it["matched_text"], it["passage"],
             it["url"], site_proof, source_id))
        new += bool(cur.fetchone()[0])
    cur.execute(
        """INSERT INTO signal_runs (company_id, searched_at, pages_read, outcome, items_found) VALUES (%s, now(), %s, %s, %s)
           ON CONFLICT (company_id) DO UPDATE SET searched_at = now(), pages_read = EXCLUDED.pages_read,
             outcome = EXCLUDED.outcome, items_found = EXCLUDED.items_found""",
        (company_id, result["pages_read"], result["outcome"], len(result["items"])))
    return new


def run(dsn: str, client=None, limit: int = 5, refresh_days: int = 90, retry_days: int = 7, cap: int = DAILY_CAP) -> dict:
    client = client or FirecrawlClient()
    stats = {"read": 0, "extracted": 0, "nothing_found": 0, "inaccessible": 0, "signals_added": 0, "errors": 0,
             "cap_reached": False, "stopped_no_credits": False}
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code = 'website'").fetchone()[0]
        rows = conn.execute(SELECT_DUE, {"days": refresh_days, "retry": retry_days, "limit": limit}).fetchall()
        conn.commit()
        for company_id, siren, value, site_proof in rows:
            if usage.used_today(conn, PROVIDER)[1] + MAX_PAGES > cap:      # never start a site that cannot be read to the end
                stats["cap_reached"] = True
                break
            stats["read"] += 1
            try:
                res = read_site(client, _url(value), reserve=lambda: usage.reserve(conn, PROVIDER, cap, 1))
            except QuotaExhausted as exc:
                log.error("%s", exc)
                stats["stopped_no_credits"] = True
                break
            except Exception as exc:
                stats["errors"] += 1
                log.warning("site signals failed for %s: %s", siren, exc)
                continue
            if res["cap_reached"]:
                stats["cap_reached"] = True       # a reserve was refused mid-site: do not record a partial read
                if res["outcome"] == "inaccessible":
                    break
            with conn.cursor() as cur:
                stats["signals_added"] += store(cur, company_id, res, site_proof, source_id) if not res["cap_reached"] else 0
            conn.commit()
            stats[res["outcome"]] += 1
            log.info("%s: %s, %d signal(s), %d page(s)", siren, res["outcome"], len(res["items"]), res["pages_read"])
            if res["cap_reached"]:
                break
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="companies per run (paid: start with 5)")
    ap.add_argument("--refresh-days", type=int, default=90)
    ap.add_argument("--retry-days", type=int, default=7, help="delay before an 'inaccessible' site is read again")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days, retry_days=a.retry_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
