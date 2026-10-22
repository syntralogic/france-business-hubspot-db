"""Export the whole database as ONE self-contained HTML file (data embedded, works offline, instant search).

Why: the live viewer asks the hosted API for every page, which is slow (a free Render service sleeps). This writes a single
file you double-click: no server, no internet, no waiting.

Usage:  DATABASE_URL=postgresql://... PYTHONPATH=src python -m fbdb.export.viewer_file [--out data/fbdb_viewer.html] [--limit N]

Same safety rules as the public API (fbdb.api): business fields only, no phone, no email, no LinkedIn, no director names,
no Google data; sole proprietors are left out; only active, diffusible units. Keep the file private anyway.
"""
from __future__ import annotations

import argparse
import datetime
import decimal
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from fbdb.api import COLUMNS, FROM

log = logging.getLogger("fbdb.export.viewer_file")
PER_COMPANY_CAP = {"financials": 5, "certificates": 50, "activities": 100, "declarations": 50, "relations": 20, "events": 20}

_JOIN = "JOIN companies c ON c.company_id = {a}.company_id"
DETAIL_QUERIES = {
    "financials": ("SELECT c.siren, f.fiscal_year, f.period_end, f.duration_months, f.revenue_eur, f.net_result_eur, f.equity_eur, "
                   "f.cash_eur, f.debt_eur, f.employees FROM company_financials f " + _JOIN.format(a="f") +
                   " ORDER BY f.fiscal_year DESC, f.period_end DESC NULLS LAST"),
    "certificates": ("SELECT c.siren, t.cert_type, t.cert_label, t.valid_from, t.valid_to, t.is_current, t.certificate_url, t.has_attestation "
                     "FROM v_company_certificates t " + _JOIN.format(a="t") + " ORDER BY t.is_current DESC, t.cert_type, t.valid_to DESC NULLS LAST"),
    "activities": ("SELECT c.siren, t.facet, t.label, t.source_url FROM company_activities t " + _JOIN.format(a="t") + " ORDER BY t.facet, t.label"),
    "declarations": ("SELECT c.siren, t.kind, t.value, t.number, t.qualifier, t.source_url FROM company_site_declarations t " +
                     _JOIN.format(a="t") + " ORDER BY t.kind, t.value"),
    "relations": ("SELECT c.siren, t.related_siren, t.related_name, t.related_in_base, t.role, t.is_active FROM v_company_relations t " +
                  _JOIN.format(a="t") + " ORDER BY t.is_active DESC, t.related_name"),
    "events": ("SELECT c.siren, t.event_type, t.event_date, t.detail, t.basis FROM company_register_events t " +
               _JOIN.format(a="t") + " ORDER BY t.event_date DESC"),
}


def _json_default(o):
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    raise TypeError(f"not serialisable: {type(o)}")


def to_json(obj) -> str:
    """JSON that is safe inside a <script> tag (no closing-tag or comment sequence can appear)."""
    s = json.dumps(obj, default=_json_default, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def collect(conn, limit: int | None = None) -> dict:
    """Read establishments and, per SIREN, the detail sections. Server-side cursors keep memory flat."""
    where = f" ORDER BY v.legal_name, v.siret" + (f" LIMIT {int(limit)}" if limit else "")
    with conn.cursor(name="viewer_rows", row_factory=dict_row) as cur:
        cur.itersize = 5000
        cur.execute(f"SELECT {COLUMNS} {FROM}{where}")
        rows = list(cur)
    sirens = {r["siren"] for r in rows}
    details: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for name, sql in DETAIL_QUERIES.items():
        cap = PER_COMPANY_CAP[name]
        with conn.cursor(name=f"viewer_{name}", row_factory=dict_row) as cur:
            cur.itersize = 5000
            cur.execute(sql)
            for r in cur:
                s = r.pop("siren")
                if s in sirens and len(details[s][name]) < cap:
                    details[s][name].append(r)
    return {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "rows": rows, "details": details}


def render(data: dict) -> str:
    return TEMPLATE.replace("__DATA__", to_json(data))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("data/fbdb_viewer.html"))
    ap.add_argument("--limit", type=int, default=None, help="only the first N establishments (for a quick test)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"], options="-c statement_timeout=0") as conn:
        conn.read_only = True
        data = collect(conn, args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data), encoding="utf-8")
    log.info("Wrote %s: %d establishments, %d companies with details, %.1f MB",
             args.out, len(data["rows"]), len(data["details"]), args.out.stat().st_size / 1e6)


TEMPLATE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FBDB Viewer (offline)</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif;color:#111827;background:#f3f4f6}
header{background:#111827;color:#fff;padding:14px 24px}h1{margin:0;font-size:20px}header small{color:#9ca3af}
main{padding:20px 24px;max-width:1500px;margin:auto}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 16px;min-width:150px}
.card span{display:block;font-size:11px;text-transform:uppercase;color:#6b7280}.card b{font-size:22px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
input[type=text]{padding:8px 10px;border:1px solid #d1d5db;border-radius:6px}button{padding:8px 14px;border:0;border-radius:6px;background:#111827;color:#fff;cursor:pointer}
button.alt{background:#fff;color:#111827;border:1px solid #d1d5db}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e5e7eb}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #f0f0f0;vertical-align:top}
th{font-size:11px;text-transform:uppercase;color:#6b7280;position:sticky;top:0;background:#fff}tr.r{cursor:pointer}tr.r:hover{background:#f9fafb}
.pg{display:flex;gap:10px;align-items:center;margin:12px 0}
#dr{position:fixed;top:0;right:0;bottom:0;width:min(520px,100%);background:#fff;box-shadow:-4px 0 20px rgba(0,0,0,.2);padding:20px;overflow:auto;display:none}
#dr h3{margin:0}#dr h4{margin:18px 0 4px;font-size:12px;text-transform:uppercase;color:#64748b}#dr .n{color:#94a3b8}#dr li{margin:3px 0}#dr a{color:#2563eb}
#dr .x{float:right}.sm{font-size:12px;color:#64748b}
</style></head><body>
<header><h1>FBDB Viewer <small>offline, one file</small></h1></header>
<main>
<div class="cards" id="cards"></div>
<div class="bar">
<input type="text" id="q" placeholder="Name, SIREN or SIRET"><input type="text" id="dept" placeholder="Dept" size="5"><input type="text" id="city" placeholder="City">
<label><input type="checkbox" id="rge"> RGE only</label><button id="go">Search</button><button class="alt" id="csv">Download results (CSV)</button>
</div>
<div class="sm" id="info"></div>
<table><thead><tr><th>Company</th><th>SIRET</th><th>City</th><th>Postal</th><th>Dept</th><th>NAF</th><th>Trade</th><th>RGE</th><th>Website</th><th>Revenue</th></tr></thead><tbody id="body"></tbody></table>
<div class="pg"><button class="alt" id="prev">Prev</button><span id="pn"></span><button class="alt" id="next">Next</button></div>
</main>
<aside id="dr"></aside>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent),R=D.rows,DET=D.details,PER=50,$=id=>document.getElementById(id);
const norm=s=>(s||'').toString().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
R.forEach(r=>{r._n=norm([r.legal_name,r.trading_name,r.establishment_name].join(' '));r._c=norm(r.city);r._rge=!!(r.rge_labels||r.certifications)});
let F=R,page=1;
const el=(t,txt,c)=>{const e=document.createElement(t);if(txt!=null)e.textContent=txt;if(c)e.className=c;return e};
const fmt=n=>n==null?'':Number(n).toLocaleString('fr-FR');
$('cards').append(...[['Establishments',R.length],['Companies',new Set(R.map(r=>r.siren)).size],['RGE certified',R.filter(r=>r._rge).length],['With website',R.filter(r=>r.website).length],['With details',Object.keys(DET).length]]
 .map(([a,b])=>{const c=el('div',null,'card');c.append(el('span',a),el('b',fmt(b)));return c}));
$('info').textContent='Data exported '+D.generated;
function search(){const q=norm($('q').value.trim()),d=$('dept').value.trim().toUpperCase(),c=norm($('city').value.trim()),g=$('rge').checked,dg=q.replace(/\s/g,'');
 F=R.filter(r=>(!q||(/^\d{9}$|^\d{14}$/.test(dg)?(r.siren===dg||r.siret===dg):r._n.includes(q)))&&(!d||r.department_code===d)&&(!c||r._c.includes(c))&&(!g||r._rge));page=1;draw()}
function draw(){const b=$('body');b.replaceChildren();const pages=Math.max(1,Math.ceil(F.length/PER));page=Math.min(page,pages);
 F.slice((page-1)*PER,page*PER).forEach(r=>{const t=el('tr',null,'r');[r.legal_name,r.siret,r.city,r.postal_code,r.department_code,r.naf,r.trade,r._rge?'yes':'',r.website,r.revenue!=null?fmt(r.revenue)+(r.revenue_year?' ('+r.revenue_year+')':''):''].forEach(v=>t.append(el('td',v)));
  t.onclick=()=>show(r);b.append(t)});$('pn').textContent='Page '+page+' / '+pages+' ('+fmt(F.length)+' results)'}
function sec(dr,title,items,fn){dr.append(el('h4',title));if(!items||!items.length){dr.append(el('div','Nothing recorded.','n'));return}const ul=el('ul');items.forEach(i=>{const li=el('li');fn(li,i);ul.append(li)});dr.append(ul)}
function link(li,txt,u){if(u&&/^https?:/i.test(u)){const a=el('a',txt);a.href=u;a.target='_blank';a.rel='noopener';li.append(a)}else li.append(txt)}
function show(r){const dr=$('dr'),d=DET[r.siren]||{};dr.replaceChildren();const x=el('button','Close','x alt');x.onclick=()=>dr.style.display='none';dr.append(x,el('h3',r.legal_name),el('div','SIREN '+r.siren+' · SIRET '+r.siret,'sm'));
 sec(dr,'Financial exercises',d.financials,(li,f)=>li.append(f.fiscal_year+': revenue '+fmt(f.revenue_eur)+', result '+fmt(f.net_result_eur)+', equity '+fmt(f.equity_eur)+', cash '+fmt(f.cash_eur)+', debt '+fmt(f.debt_eur)+(f.employees!=null?', staff '+f.employees:'')));
 sec(dr,'Certificates and insurance',d.certificates,(li,c)=>{link(li,(c.cert_label||c.cert_type),c.certificate_url);li.append(' until '+(c.valid_to||'unknown')+(c.has_attestation?' · attestation on file':''))});
 sec(dr,'Activities read on the site',d.activities,(li,a)=>link(li,a.facet+': '+a.label,a.source_url));
 sec(dr,'Declared on the site',d.declarations,(li,a)=>link(li,a.kind+': '+a.value+(a.qualifier?' ('+a.qualifier+')':''),a.source_url));
 sec(dr,'Legal relations (register)',d.relations,(li,a)=>li.append((a.related_name||a.related_siren)+' ('+a.related_siren+') '+a.role+(a.is_active?'':' · ended')));
 sec(dr,'Register events',d.events,(li,a)=>li.append((a.event_date||'')+' '+a.event_type+(a.detail?': '+a.detail:'')));
 dr.style.display='block'}
function csv(){const cols=Object.keys(R[0]||{}).filter(k=>k[0]!=='_'),q=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"';
 const t=[cols.join(',')].concat(F.map(r=>cols.map(c=>q(r[c])).join(','))).join('\n'),a=el('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+t],{type:'text/csv'}));a.download='fbdb_results.csv';a.click()}
$('go').onclick=search;['q','dept','city'].forEach(i=>$(i).addEventListener('keydown',e=>e.key==='Enter'&&search()));$('rge').onchange=search;
$('prev').onclick=()=>{page=Math.max(1,page-1);draw()};$('next').onclick=()=>{page++;draw()};$('csv').onclick=csv;draw();
</script></body></html>
"""

if __name__ == "__main__":
    main()
