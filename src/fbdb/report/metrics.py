"""Acceptance metrics: coverage, fill rate, match rate, gaps against targets, short client report.

Definitions (all measured on what the HubSpot export would contain, i.e. view v_hubspot_companies):
  coverage    = exportable establishments / active establishments held in the base; also reported against the
                agreed volume target (--target-rows, default 500,000, the low end of the brief).
  fill rate   = rows where the field is non-empty / exportable rows, per export column.
  match rate  = share of the relevant population for which an enrichment source returned data:
                geocoding (address verified at score >= threshold, coordinates present), directors (active, diffusible,
                non-sole-proprietor companies with >= 1 active director), financials (companies with revenue),
                RGE (establishments with a valid certificate), Google (establishments with a matched rating),
                licensed phone/email (exportable rows with a verified/sourced value).
Targets live in DEFAULT_TARGETS (fractions). They are PROPOSED values for registry-grade fields; a None target means
"report only", as the brief says for phone/email, revenue, directors and ratings. Override with --targets file.json.

Outputs in --out (default reports/metrics): metrics.json, fill_rates.csv, client_report.md.
Usage: python -m fbdb.report.metrics [--out reports/metrics] [--target-rows 500000] [--targets t.json]
Needs: DATABASE_URL.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg
from psycopg import sql

log = logging.getLogger("fbdb.metrics")

SKIP_COLUMNS = {"internal_id"}
DEFAULT_TARGETS: dict[str, Optional[float]] = {
    # registry-grade (official source): proposed gate
    "siret": 1.0, "siren": 1.0, "legal_name": 0.99, "naf": 0.99, "status": 1.0, "creation_date": 0.95,
    "street": 0.85, "postal_code": 0.99, "city": 0.99, "department_name": 0.99, "region": 0.99,
    "legal_form": 0.95, "primary_source": 1.0, "confidence": 0.99, "last_verified_at": 0.95,
    # enrichment: report only unless the client sets a number
    "phone": None, "email": None, "website": None, "linkedin": None, "revenue": None, "employees": None,
    "director_last": None, "rge_labels": None, "google_rating": None, "trade": None,
    # match rates (keys prefixed match_)
    "match_geocode_verified": 0.85,
}


def fill_status(rate: float, target: Optional[float]) -> str:
    if target is None:
        return "REPORT"
    return "PASS" if rate >= target else "GAP"


def evaluate(rates: dict[str, float], targets: dict[str, Optional[float]]) -> list[dict]:
    rows = []
    for k, r in rates.items():
        t = targets.get(k)
        rows.append({"metric": k, "rate": r, "target": t, "status": fill_status(r, t),
                     "gap": (t - r) if (t is not None and r < t) else 0.0})
    return rows


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_report(m: dict, rows: list[dict], today: Optional[date] = None) -> str:
    today = today or date.today()
    gaps = sorted((r for r in rows if r["status"] == "GAP"), key=lambda r: -r["gap"])
    out = [f"# Data quality report: France Business DB ({today.isoformat()})", "",
           "## Volume and coverage",
           f"- Exportable establishments: **{m['exportable']:,}** of {m['active']:,} active in the base "
           f"(coverage {_pct(m['coverage'])}).",
           f"- Against the volume target of {m['target_rows']:,}: **{_pct(m['volume_vs_target'])}**.", "",
           "## Gaps against targets"]
    out += ([f"- `{g['metric']}`: {_pct(g['rate'])} vs target {_pct(g['target'])} (short by {_pct(g['gap'])})" for g in gaps]
            or ["- None: every metric with a target meets it."])
    out += ["", "## Enrichment fill and match rates (reported, no target agreed)"]
    out += [f"- `{r['metric']}`: {_pct(r['rate'])}" for r in rows if r["status"] == "REPORT"]
    out += ["", "## Reading notes",
            "- Registry fields come from SIRENE (official). Phone and email only exist where a licensed provider delivered them.",
            "- Revenue exists only where accounts are public; directors are mostly absent for sole proprietors by design.",
            "- Full per-field table: `fill_rates.csv`. Targets marked REPORT have no agreed threshold yet.", ""]
    return "\n".join(out)


def collect(dsn: str, threshold_note: float = 0.6, target_rows: int = 500_000) -> tuple[dict, dict[str, float]]:
    with psycopg.connect(dsn) as conn:
        active = conn.execute("SELECT count(*) FROM company_addresses WHERE status='active'").fetchone()[0]
        cols = [d.name for d in conn.execute("SELECT * FROM v_hubspot_companies LIMIT 0").description if d.name not in SKIP_COLUMNS]
        selects = [sql.SQL("count(*)")] + [
            sql.SQL("count(NULLIF(btrim({}::text), ''))").format(sql.Identifier(c)) for c in cols]
        row = conn.execute(sql.SQL("SELECT {} FROM v_hubspot_companies WHERE status='active'").format(sql.SQL(", ").join(selects))).fetchone()
        exportable = row[0]
        rates = {c: (row[i + 1] / exportable if exportable else 0.0) for i, c in enumerate(cols)}

        def one(q: str) -> tuple:
            return conn.execute(q).fetchone()

        v, with_xy = one("""SELECT count(*) FILTER (WHERE a.address_status='verified'),
                                   count(*) FILTER (WHERE a.latitude IS NOT NULL AND a.longitude IS NOT NULL)
                            FROM company_addresses a JOIN v_hubspot_companies v ON v.internal_id=a.establishment_id AND v.status='active'""")
        pop, with_dir = one("""SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_directors d
                                 WHERE d.company_id=c.company_id AND d.is_active))
                               FROM companies c WHERE c.status='active' AND c.is_diffusible AND NOT c.is_sole_proprietor""")
        rge_est = one("""SELECT count(DISTINCT x.establishment_id) FROM company_certifications x
                         JOIN v_hubspot_companies v ON v.internal_id=x.establishment_id AND v.status='active'
                         WHERE x.cert_type='RGE' AND (x.valid_to IS NULL OR x.valid_to>=current_date)""")[0]
        div = (lambda n, d: n / d if d else 0.0)
        rates["match_geocode_verified"] = div(v, exportable)
        rates["match_geocode_coordinates"] = div(with_xy, exportable)
        rates["match_directors_non_sole_prop_companies"] = div(with_dir, pop)
        rates["match_rge_establishments"] = div(rge_est, exportable)
        # First-test-lot KPIs asked by the client (company level; report only). Denominators are named in the metric.
        comp, proven, with_person, social = one("""SELECT count(*),
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_contacts k WHERE k.company_id=c.company_id AND k.contact_type='website'
                                             AND k.match_proof IS NOT NULL)),
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_interlocutors i WHERE i.company_id=c.company_id AND i.is_active)),
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_contacts k WHERE k.company_id=c.company_id
                                             AND k.contact_type IN ('social','linkedin') AND k.value_status<>'unavailable'))
            FROM companies c WHERE c.status='active' AND c.is_diffusible""")
        dirs, d_mobile, d_email, d_email_any, d_conf = one("""SELECT count(*),
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_contacts k WHERE k.interlocutor_id=i.interlocutor_id
                    AND k.contact_type='phone' AND k.value_normalized ~ '^\\+33[67]' AND k.value_status IN ('verified','sourced'))),
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_contacts k WHERE k.interlocutor_id=i.interlocutor_id
                    AND k.contact_type='email' AND k.value_status='verified')),
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM company_contacts k WHERE k.interlocutor_id=i.interlocutor_id
                    AND k.contact_type='email' AND k.value_status<>'unavailable')),
              count(*) FILTER (WHERE i.decision_maker_confirmed)
            FROM company_interlocutors i JOIN companies c ON c.company_id=i.company_id AND c.status='active' AND c.is_diffusible
            WHERE i.is_active AND i.function_category='director'""")
        rates["match_companies_with_proven_website"] = div(proven, comp)
        rates["match_companies_with_interlocutor"] = div(with_person, comp)
        rates["match_companies_with_social_account"] = div(social, comp)     # confirmed accounts only (site link or proven by search)
        # candidates found by search but NOT proven (social_searches.status = 'to_verify'), companies with no confirmed account
        to_verify = one("""SELECT count(*) FROM companies c WHERE c.status='active' AND c.is_diffusible
              AND EXISTS (SELECT 1 FROM social_searches s WHERE s.company_id=c.company_id AND s.status='to_verify')
              AND NOT EXISTS (SELECT 1 FROM company_contacts k WHERE k.company_id=c.company_id
                              AND k.contact_type IN ('social','linkedin') AND k.value_status<>'unavailable')""")[0]
        rates["match_companies_with_social_to_verify"] = div(to_verify, comp)
        # claims read from the proven site (company_activities): a company with a site but no claim is "nothing found", not "no activity"
        with_claims = one("""SELECT count(*) FROM companies c WHERE c.status='active' AND c.is_diffusible
              AND EXISTS (SELECT 1 FROM company_activities a WHERE a.company_id=c.company_id)""")[0]
        rates["match_companies_with_activity_claims"] = div(with_claims, comp)
        rates["match_directors_with_mobile_linked"] = div(d_mobile, dirs)          # mobile attached to the person (with evidence), not just the company
        rates["match_directors_with_verified_email_linked"] = div(d_email, dirs)   # deliverable per verifier + attached to the person
        rates["match_directors_with_any_email_linked"] = div(d_email_any, dirs)    # includes provider hypotheses ('inferred')
        rates["match_directors_decision_maker_confirmed"] = div(d_conf, dirs)
    m = {"active": active, "exportable": exportable, "coverage": (exportable / active if active else 0.0),
         "target_rows": target_rows, "volume_vs_target": exportable / target_rows if target_rows else 0.0,
         "geocode_threshold": threshold_note}
    return m, rates


def write_outputs(out_dir: str | Path, m: dict, rows: list[dict]) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps({"summary": m, "metrics": rows}, indent=2, default=str), encoding="utf-8")
    with open(out / "fill_rates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "rate_pct", "target_pct", "status", "gap_pct"])
        for r in rows:
            w.writerow([r["metric"], f"{r['rate'] * 100:.2f}", "" if r["target"] is None else f"{r['target'] * 100:.1f}",
                        r["status"], f"{r['gap'] * 100:.2f}"])
    (out / "client_report.md").write_text(render_report(m, rows), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/metrics")
    ap.add_argument("--target-rows", type=int, default=500_000)
    ap.add_argument("--targets", help="JSON file {metric: fraction or null} overriding DEFAULT_TARGETS")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    targets = dict(DEFAULT_TARGETS)
    if a.targets:
        targets.update(json.loads(Path(a.targets).read_text(encoding="utf-8")))
    m, rates = collect(os.environ["DATABASE_URL"], target_rows=a.target_rows)
    rows = evaluate(rates, targets)
    print(write_outputs(a.out, m, rows))
    print({"exportable": m["exportable"], "coverage": round(m["coverage"], 4),
           "gaps": [r["metric"] for r in rows if r["status"] == "GAP"]})


if __name__ == "__main__":
    main()
