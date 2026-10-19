"""What the company says it does, read from the pages of its OWN site proven by SIREN (client spec, section 3). PAID (Firecrawl credits).

Only a website contact that carries a SIREN proof (company_contacts.match_proof, set by fbdb.enrich.website) is read: a site we
could not prove is never a source. Per company: the home page + up to 3 same-site pages that look like "services / prestations /
metiers / activites / depannage / entretien / a-propos" (blog, news, hiring and legal pages are skipped: an article is not a service).
The text is classified by the keyword rules of fbdb.enrich.activity_rules into trade, services, specialties, work type (neuf /
renovation / depannage / maintenance), client types, contracts (entretien, urgence 24/7), brands and approvals CLAIMED by the site.

Every stored claim (company_activities) keeps the passage that justifies it, the words matched, the page URL, the date and the SIREN
proof of the site. Photos are never read (images, alt text included, are removed), nothing is inferred from a photo or a gallery, and
a claimed approval (RGE, Qualibat...) is NOT an official certification: it never goes into company_certifications.

Absence is never "does not do it": activity_runs.outcome is extracted / nothing_found (pages read, no rule matched) / inaccessible
(home page unreadable, retried after --retry-days); no row = not read yet. A claim is never deleted because a later read no longer
finds it (only last_seen_at moves). The first passage found is kept.

Cost: at most 4 scrapes per company (home + 3), counted in FIRECRAWL_DAILY_CAP (shared with fbdb.enrich.website); a company is not
started unless 4 credits are left, so a run never stops in the middle of a site. Scrape format and credit cost are NOT verified live
(see fbdb.enrich.website): run `--limit 5` first and read the log. The keyword vocabulary is a first version, not measured on real
sites: label a sample before targeting on it.

Usage: python -m fbdb.enrich.site_activity [--limit 5] [--refresh-days 180] [--retry-days 7]. Needs DATABASE_URL, FIRECRAWL_API_KEY.
"""
from __future__ import annotations

import argparse
import logging
import os
from urllib.parse import urljoin, urlparse

import psycopg

from fbdb.common import usage
from fbdb.enrich.activity_rules import extract_activities, merge_pages
from fbdb.enrich.declaration_rules import extract_declarations, merge_declarations
from fbdb.enrich.website import DAILY_CAP, PROVIDER, FirecrawlClient, QuotaExhausted

log = logging.getLogger("fbdb.site_activity")
MAX_EXTRA_PAGES = 3
MAX_PAGES = 1 + MAX_EXTRA_PAGES
# path hints of pages that describe the offer, best first
SERVICE_HINTS = ("prestation", "service", "metier", "activite", "expertise", "competence", "specialite", "domaine", "depannage",
                 "installation", "entretien", "renovation", "contrat", "nos-solutions", "solutions", "a-propos", "apropos",
                 "qui-sommes-nous", "societe", "entreprise", "about")
# pages that are never read for activity
SKIP_HINTS = ("blog", "actualit", "news", "article", "conseil", "astuce", "guide", "recrut", "emploi", "carriere", "offre-d",
              "mention", "legal", "cgv", "cgu", "politique", "confidentialite", "cookie", "plan-du-site", "sitemap", "contact",
              "devis", "avis", "faq", "login", "panier", "boutique", "shop", "produit")


def activity_pages(base_url: str, links: list[str], limit: int = MAX_EXTRA_PAGES) -> list[str]:
    """Same-site pages that describe the offer, best hint first. The home page itself is excluded."""
    host = (urlparse(base_url).hostname or "").removeprefix("www.")
    scored, seen = [], set()
    for link in links or []:
        full = urljoin(base_url, link).split("#")[0].split("?")[0]
        p = urlparse(full)
        if p.scheme not in ("http", "https") or (p.hostname or "").removeprefix("www.") != host:
            continue
        path = p.path.lower().rstrip("/")
        if not path or full.rstrip("/") == base_url.rstrip("/") or full in seen:
            continue
        if any(h in path for h in SKIP_HINTS) or path.rsplit(".", 1)[-1] in ("pdf", "jpg", "jpeg", "png", "gif", "webp", "zip"):
            continue
        for rank, hint in enumerate(SERVICE_HINTS):
            if hint in path:
                seen.add(full)
                scored.append((rank, path.count("/"), full))
                break
    return [u for _, _, u in sorted(scored)][:limit]


def read_site(client, site_url: str, reserve=lambda: True) -> dict:
    """Scrape the home page then the offer pages, classify. Pure of the database.

    Returns {"outcome": "extracted"|"nothing_found"|"inaccessible", "items": [...], "pages_read": n, "cap_reached": bool}.
    Items are merged page by page (home first): the first page that states a claim keeps it.
    """
    out = {"outcome": "inaccessible", "items": [], "declarations": [], "pages_read": 0, "cap_reached": False}
    if not reserve():
        out["cap_reached"] = True
        return out
    home = client.scrape(site_url)
    out["pages_read"] = 1 if home else 0
    if not home:
        return out
    per_page = [extract_activities(home["markdown"], site_url)]
    decl_pages = [extract_declarations(home["markdown"], site_url)]
    for url in activity_pages(site_url, home["links"]):
        if not reserve():
            out["cap_reached"] = True
            break
        page = client.scrape(url)
        if not page:
            continue
        out["pages_read"] += 1
        per_page.append(extract_activities(page["markdown"], url))
        decl_pages.append(extract_declarations(page["markdown"], url))
    out["items"] = merge_pages(per_page)
    out["declarations"] = merge_declarations(decl_pages)
    out["outcome"] = "extracted" if (out["items"] or out["declarations"]) else "nothing_found"
    return out


SELECT_DUE = """
SELECT w.company_id, w.siren, w.value, w.match_proof FROM (
  SELECT DISTINCT ON (k.company_id) k.company_id, c.siren, k.value, k.match_proof
  FROM company_contacts k JOIN companies c ON c.company_id = k.company_id
  WHERE k.contact_type = 'website' AND k.match_proof IS NOT NULL AND k.value_status IN ('verified','sourced')
    AND c.status = 'active' AND c.is_diffusible
  ORDER BY k.company_id, k.is_primary DESC, k.contact_id) w
LEFT JOIN activity_runs r ON r.company_id = w.company_id
WHERE r.company_id IS NULL
   OR (r.outcome <> 'inaccessible' AND r.searched_at < now() - make_interval(days => %(days)s))
   OR (r.outcome = 'inaccessible' AND r.searched_at < now() - make_interval(days => %(retry)s))
ORDER BY r.searched_at NULLS FIRST, w.company_id LIMIT %(limit)s
"""


def _url(value: str) -> str:
    return value if value.startswith("http") else "https://" + value


def store(cur, company_id: int, result: dict, site_proof: str, source_id: int) -> int:
    """Upsert the claims and remember the read. An existing claim keeps its first passage and URL (only last_seen_at moves);
    nothing is deleted. Returns the number of NEW claims."""
    new = 0
    for d in result.get("declarations") or []:
        cur.execute(
            """INSERT INTO company_site_declarations (company_id, kind, value, number, qualifier, matched_text, passage, source_url,
                 site_proof, source_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, kind, value) DO UPDATE SET last_seen_at = now() RETURNING (xmax = 0)""",
            (company_id, d["kind"], d["value"], d["number"], d["qualifier"], d["matched_text"], d["passage"], d["url"], site_proof, source_id))
        new += bool(cur.fetchone()[0])
    for it in result["items"]:
        cur.execute(
            """INSERT INTO company_activities (company_id, facet, code, label, matched_text, passage, source_url, site_proof, source_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, facet, code) DO UPDATE SET last_seen_at = now()
               RETURNING (xmax = 0)""",
            (company_id, it["facet"], it["code"], it["label"], it["matched_text"], it["passage"], it["url"], site_proof, source_id))
        new += bool(cur.fetchone()[0])
    cur.execute(
        """INSERT INTO activity_runs (company_id, searched_at, pages_read, outcome, items_found) VALUES (%s, now(), %s, %s, %s)
           ON CONFLICT (company_id) DO UPDATE SET searched_at = now(), pages_read = EXCLUDED.pages_read,
             outcome = EXCLUDED.outcome, items_found = EXCLUDED.items_found""",
        (company_id, result["pages_read"], result["outcome"], len(result["items"]) + len(result.get("declarations") or [])))
    return new


def run(dsn: str, client=None, limit: int = 5, refresh_days: int = 180, retry_days: int = 7, cap: int = DAILY_CAP) -> dict:
    client = client or FirecrawlClient()
    stats = {"read": 0, "extracted": 0, "nothing_found": 0, "inaccessible": 0, "claims_added": 0, "errors": 0,
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
                log.warning("site activity failed for %s: %s", siren, exc)
                continue
            if res["cap_reached"]:
                stats["cap_reached"] = True       # a reserve was refused mid-site: do not record a partial read
                if res["outcome"] == "inaccessible":
                    break
            with conn.cursor() as cur:
                stats["claims_added"] += store(cur, company_id, res, site_proof, source_id) if not res["cap_reached"] else 0
            conn.commit()
            stats[res["outcome"]] += 1
            log.info("%s: %s, %d claim(s), %d declaration(s), %d page(s)", siren, res["outcome"], len(res["items"]),
                     len(res["declarations"]), res["pages_read"])
            if res["cap_reached"]:
                break
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="companies per run (paid: start with 5)")
    ap.add_argument("--refresh-days", type=int, default=180)
    ap.add_argument("--retry-days", type=int, default=7, help="delay before an 'inaccessible' site is read again")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(os.environ["DATABASE_URL"], limit=a.limit, refresh_days=a.refresh_days, retry_days=a.retry_days)
    print(s)
    if s["stopped_no_credits"] or s["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
