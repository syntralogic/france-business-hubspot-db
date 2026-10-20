"""Public contracts from the DECP open data (Donnees essentielles de la commande publique), by SIREN of the titulaire (spec section 12).

File-based loader (like fbdb.enrich.ademe_rge): download the consolidated DECP file from data.gouv.fr yourself and pass it with the
URL you got it from. JSON (`{"marches": [...]}`, a bare array, or the legacy `{"marches": {"marche": [...]}}`; read as a stream, so a
multi-GB file does not fill memory) and CSV (one row per titulaire) are read.

**NOT verified live**: the build sandbox cannot reach data.gouv.fr. Field names (id, uid, acheteur.id/nom, titulaires[].typeIdentifiant/
id/denominationSociale, objet, montant, dateNotification, datePublicationDonnees, codeCPV, procedure, nature, dureeMois,
lieuExecution) and the CSV header aliases below come from the DECP documentation. Read the first rejects file and the stats of a small
real file first; override CSV headers with --map FIELD=HEADER if they differ.

Rules:
- A contract is attached to a company ONLY through the titulaire's published identifier: a Luhn-valid SIRET (typeIdentifiant SIRET, or
  no type) or SIREN. TVA / HORS-UE identifiers are rejected, never guessed into a SIREN.
- Only companies already in our base (and diffusible) get a row; titulaires we do not hold are counted (`not_in_base`), nothing is created.
- Each row keeps decp_uid / market id / buyer and the dataset URL (`source_url`): that is the proof. Nothing else is invented.
- A later load that brings a DIFFERENT amount or notification date for a contract we already hold does not overwrite it: the
  difference goes to field_conflicts (`decp:amount:<uid>`, `decp:notification_date:<uid>`). Modifications (avenants) are not read.
- An amount <= 0 or >= 1e9 as published is stored but flagged `amount_suspect` (placeholder / typo) and must never be summed.
- "No contract" is never stored. v_public_contract_status says `found` / `none_in_loaded_datasets` / `not_checked`; the middle value
  only means "not in the files we loaded" (DECP holds what buyers published, mostly above the legal threshold, with gaps).

AUTOMATIC DOWNLOAD (no file to fetch by hand): `python -m fbdb.enrich.decp --download` resolves the newest main CSV resource of the
consolidated tabular dataset on data.gouv.fr (DECP_DATASET_ID, default 608c055b35eb4e6ee20eb325) through its API, streams it to
data/downloads/, loads it and deletes it (--keep-file keeps it). The resource URL is the source_url (proof of every row). With
--if-older-than-days N it does nothing when a DECP load younger than N days exists (the weekly run uses 30). CSV rows whose
`donneesActuelles` is false (older versions of a modified market) are skipped.

Usage: python -m fbdb.enrich.decp FILE --source-url https://www.data.gouv.fr/... [--label "DECP consolidated 2026-10"]
       [--coverage-note "..."] [--rejects data/exports/decp_rejects.csv] [--map titulaire_id=Titulaire_SIRET]
Needs: DATABASE_URL.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Optional

import psycopg

from fbdb.common.provenance import record_conflict
from fbdb.transform.normalize import clean_digits, is_valid_siren, is_valid_siret

log = logging.getLogger("fbdb.decp")
CHUNK = 1 << 20
BATCH = 2000
AMOUNT_LIMIT = Decimal("1000000000")
STORE_LIMIT = Decimal("1e14")

CSV_ALIASES = {
    "uid": ["uid"], "market_id": ["id", "id_marche", "marche_id"], "buyer_id": ["acheteur_id"],
    "buyer_name": ["acheteur_nom", "acheteur_denominationsociale"], "holder_id": ["titulaire_id", "titulaire_siret"],
    "holder_type": ["titulaire_typeidentifiant", "titulaire_type_identifiant"],
    "holder_name": ["titulaire_denominationsociale", "titulaire_nom"], "object": ["objet"], "amount": ["montant"],
    "notification_date": ["datenotification", "date_notification"],
    "publication_date": ["datepublicationdonnees", "date_publication_donnees"], "cpv_code": ["codecpv", "code_cpv"],
    "procedure": ["procedure"], "nature": ["nature"], "duration_months": ["dureemois", "duree_mois"],
    "place_code": ["lieuexecution_code"], "place_name": ["lieuexecution_nom"],
    "current": ["donneesactuelles", "donnees_actuelles"],      # tabular consolidated file: false = an older version of a modified market
}


def _key(h: str) -> str:
    h = unicodedata.normalize("NFKD", h or "").encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def parse_date(v) -> Optional[date]:
    v = str(v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v).replace(" ", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def parse_int(v) -> Optional[int]:
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def siren_of(holder_type: Optional[str], holder_id: Optional[str]) -> Optional[str]:
    """SIREN of a published titulaire identifier, or None. Never guesses: a TVA / foreign identifier is not a SIREN."""
    t = (holder_type or "").strip().upper()
    digits = clean_digits(holder_id)
    if t in ("", "SIRET") and len(digits) == 14 and is_valid_siret(digits):
        return digits[:9]
    if t in ("", "SIREN") and len(digits) == 9 and is_valid_siren(digits):
        return digits
    return None


# ---------------------------------------------------------------- reading
def _s(v) -> Optional[str]:
    v = str(v).strip() if v is not None else ""
    return v or None


def market_rows(m: dict) -> Iterator[dict]:
    """One normalized row per titulaire of a JSON market. Accepts titulaires as direct objects or wrapped in {"titulaire": {...}}."""
    holders = m.get("titulaires") or []
    if isinstance(holders, dict):
        holders = [holders]
    buyer = m.get("acheteur") or {}
    place = m.get("lieuExecution") or {}
    base = {
        "uid": _s(m.get("uid")), "market_id": _s(m.get("id")), "buyer_id": _s(buyer.get("id")), "buyer_name": _s(buyer.get("nom")),
        "object": _s(m.get("objet")), "amount": m.get("montant"), "notification_date": m.get("dateNotification"),
        "publication_date": m.get("datePublicationDonnees"), "cpv_code": _s(m.get("codeCPV")), "procedure": _s(m.get("procedure")),
        "nature": _s(m.get("nature")), "duration_months": m.get("dureeMois"),
        "place": _s(" ".join(x for x in (_s(place.get("code")), _s(place.get("nom"))) if x)) if isinstance(place, dict) else _s(place),
        "n_holders": len(holders),
    }
    for h in holders:
        h = h.get("titulaire", h) if isinstance(h, dict) else {}
        yield {**base, "holder_type": _s(h.get("typeIdentifiant")), "holder_id": _s(h.get("id")),
               "holder_name": _s(h.get("denominationSociale"))}


def iter_json_markets(path: str | Path) -> Iterator[dict]:
    """Stream the markets of a DECP JSON file without loading it whole."""
    dec = json.JSONDecoder()
    with open(path, encoding="utf-8-sig") as f:
        buf, i, started, eof, need = "", 0, False, False, True
        while True:
            if not eof and (need or len(buf) - i < CHUNK):
                chunk = f.read(CHUNK)
                if not chunk:
                    eof = True
                buf = buf[i:] + chunk
                i, need = 0, False
            if not started:
                head = buf.lstrip()
                if head.startswith("["):
                    i = len(buf) - len(head) + 1
                else:
                    k = buf.find('"marches"')
                    j = buf.find("[", k) if k != -1 else -1
                    if j == -1:
                        if eof:
                            raise ValueError("no \"marches\" array found in this JSON file")
                        need = True
                        continue
                    i = j + 1
                started = True
            while True:
                while i < len(buf) and buf[i] in " \t\r\n,":
                    i += 1
                if i >= len(buf):
                    break
                if buf[i] == "]":
                    return
                try:
                    obj, i = dec.raw_decode(buf, i)
                except json.JSONDecodeError:
                    if eof:
                        raise
                    need = True                             # incomplete object (possibly larger than a chunk): read more
                    break
                if isinstance(obj, dict):
                    yield obj
            if eof:
                return


def iter_csv_rows(path: str | Path, mapping: Optional[dict] = None) -> Iterator[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = f.readline()
        f.seek(0)
        delim = ";" if head.count(";") > head.count(",") else ","
        rd = csv.DictReader(f, delimiter=delim)
        cols = {_key(c): c for c in (rd.fieldnames or [])}
        pick = {}
        for k, al in CSV_ALIASES.items():
            override = (mapping or {}).get(k)
            pick[k] = (cols.get(_key(override)) if override else None) or next((cols[a] for a in al if a in cols), None)
        if not pick["holder_id"]:
            raise ValueError(f"no titulaire identifier column found; headers: {list(cols)} (use --map holder_id=HEADER)")
        for r in rd:
            g = lambda k: _s(r.get(pick[k])) if pick[k] else None  # noqa: E731
            if (g("current") or "").lower() in ("false", "0", "faux", "non", "f"):
                continue            # an earlier version of a market that was modified: the current row carries the market
            place = " ".join(x for x in (g("place_code"), g("place_name")) if x) or None
            yield {"uid": g("uid"), "market_id": g("market_id"), "buyer_id": g("buyer_id"), "buyer_name": g("buyer_name"),
                   "object": g("object"), "amount": g("amount"), "notification_date": g("notification_date"),
                   "publication_date": g("publication_date"), "cpv_code": g("cpv_code"), "procedure": g("procedure"),
                   "nature": g("nature"), "duration_months": g("duration_months"), "place": place, "n_holders": None,
                   "holder_type": g("holder_type"), "holder_id": g("holder_id"), "holder_name": g("holder_name")}


def iter_rows(path: str | Path, mapping: Optional[dict] = None) -> Iterator[dict]:
    if str(path).lower().endswith((".csv", ".tsv", ".txt")):
        yield from iter_csv_rows(path, mapping)
    else:
        for m in iter_json_markets(path):
            yield from market_rows(m)


def prepare(row: dict) -> tuple[Optional[dict], Optional[str]]:
    """(clean row, None) or (None, reject reason). The reason is written to the rejects file."""
    siren = siren_of(row.get("holder_type"), row.get("holder_id"))
    if not siren:
        return None, "INVALID_TITULAIRE_ID"
    uid = row.get("uid") or (f"{row['buyer_id']}{row['market_id']}" if row.get("buyer_id") and row.get("market_id") else None)
    if not uid:
        return None, "NO_UID"
    amount = parse_amount(row.get("amount"))
    too_big = amount is not None and abs(amount) >= STORE_LIMIT      # cannot be stored (NUMERIC(16,2)): not stored, but flagged
    if too_big:
        amount = None
    return {**row, "siren": siren, "decp_uid": uid, "amount": amount,
            "amount_suspect": too_big or (amount is not None and (amount <= 0 or amount >= AMOUNT_LIMIT)),
            "notification_date": parse_date(row.get("notification_date")), "publication_date": parse_date(row.get("publication_date")),
            "duration_months": parse_int(row.get("duration_months"))}, None


# ---------------------------------------------------------------- loading
INSERT = """INSERT INTO public_contracts (company_id, siren, decp_uid, market_id, buyer_id, buyer_name, object, amount_eur, amount_suspect,
              notification_date, publication_date, cpv_code, procedure, nature, duration_months, execution_place, titulaire_name,
              co_holders, load_id, source_id, source_url)
            VALUES (%(company_id)s,%(siren)s,%(decp_uid)s,%(market_id)s,%(buyer_id)s,%(buyer_name)s,%(object)s,%(amount)s,%(amount_suspect)s,
              %(notification_date)s,%(publication_date)s,%(cpv_code)s,%(procedure)s,%(nature)s,%(duration_months)s,%(place)s,%(holder_name)s,
              %(n_holders)s,%(load_id)s,%(source_id)s,%(source_url)s)
            ON CONFLICT (company_id, decp_uid) DO NOTHING RETURNING contract_id"""


def _store(conn, r: dict, ctx: dict, stats: dict) -> None:
    cur = conn.execute(INSERT, {**r, **ctx})
    if cur.fetchone():
        stats["loaded"] += 1
        return
    old = conn.execute("SELECT amount_eur, notification_date FROM public_contracts WHERE company_id=%s AND decp_uid=%s",
                       (r["company_id"], r["decp_uid"])).fetchone()
    differs = False
    new_amount = r["amount"].quantize(Decimal("0.01")) if r["amount"] is not None else None       # same format as the stored value
    for name, old_v, new_v in (("amount", old[0], new_amount), ("notification_date", old[1], r["notification_date"])):
        if new_v is not None and old_v is not None and old_v != new_v:
            record_conflict(conn, r["company_id"], f"decp:{name}:{r['decp_uid']}", old_v, ctx["source_id"], new_v, ctx["source_id"])
            differs = True
    stats["conflict" if differs else "duplicate"] += 1


def run(dsn: str, path: str, source_url: str, label: Optional[str] = None, coverage_note: Optional[str] = None,
        rejects_path: Optional[str] = None, mapping: Optional[dict] = None, batch: int = BATCH) -> dict:
    if not source_url:
        raise ValueError("source_url is required: it is the proof of every contract of this file")
    stats = {"read": 0, "loaded": 0, "duplicate": 0, "conflict": 0, "rejected": 0, "not_in_base": 0}
    rejects: list[tuple[str, str, str]] = []
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute("SELECT source_id FROM data_sources WHERE code='decp'").fetchone()[0]
        load_id = conn.execute(
            "INSERT INTO decp_loads (source_label, source_url, file_name, coverage_note) VALUES (%s,%s,%s,%s) RETURNING load_id",
            (label or f"DECP {Path(path).name}", source_url, Path(path).name, coverage_note)).fetchone()[0]
        ctx = {"load_id": load_id, "source_id": source_id, "source_url": source_url}
        dates: list[date] = []

        def flush(rows: list[dict]) -> None:
            ids = dict(conn.execute("SELECT siren, company_id FROM companies WHERE siren = ANY(%s) AND is_diffusible",
                                    ([r["siren"] for r in rows],)).fetchall())
            for r in rows:
                cid = ids.get(r["siren"])
                if cid is None:
                    stats["not_in_base"] += 1
                    continue
                r["company_id"] = cid
                _store(conn, r, ctx, stats)
                if r["notification_date"]:
                    dates.append(r["notification_date"])
            conn.commit()

        pending: list[dict] = []
        for raw in iter_rows(path, mapping):
            stats["read"] += 1
            r, why = prepare(raw)
            if r is None:
                stats["rejected"] += 1
                rejects.append((str(raw.get("holder_id") or ""), why, str(raw.get("uid") or raw.get("market_id") or "")))
                continue
            pending.append(r)
            if len(pending) >= batch:
                flush(pending)
                pending = []
        if pending:
            flush(pending)
        conn.execute(
            """UPDATE decp_loads SET rows_read=%s, rows_loaded=%s, rows_duplicate=%s, rows_conflict=%s, rows_rejected=%s,
                      rows_not_in_base=%s, notified_from=%s, notified_to=%s WHERE load_id=%s""",
            (stats["read"], stats["loaded"], stats["duplicate"], stats["conflict"], stats["rejected"], stats["not_in_base"],
             min(dates) if dates else None, max(dates) if dates else None, load_id))
        conn.commit()
    if rejects_path and rejects:
        Path(rejects_path).parent.mkdir(parents=True, exist_ok=True)
        with open(rejects_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["titulaire_id", "reason", "market"])
            w.writerows(rejects)
    return stats


DEFAULT_DATASET = "608c055b35eb4e6ee20eb325"     # "DECP consolidees (format tabulaire)", data.gouv.fr


def _is_due(dsn: str, days: int) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT max(loaded_at) < now() - make_interval(days => %s) FROM decp_loads", (days,)).fetchone()
    return row[0] is None or bool(row[0])           # no load yet, or the newest one is old enough


def download_and_run(dsn: str, dataset: str, fmt: str = "csv", title_contains: Optional[str] = None, label: Optional[str] = None,
                     coverage_note: Optional[str] = None, rejects_path: Optional[str] = None, mapping: Optional[dict] = None,
                     work_dir: str = "data/downloads", keep: bool = False, if_older_than_days: Optional[int] = None,
                     client=None) -> dict:
    from fbdb.common import opendata
    if if_older_than_days is not None and not _is_due(dsn, if_older_than_days):
        return {"skipped": f"a DECP load younger than {if_older_than_days} days exists"}
    if fmt.lower() not in ("csv", "json"):
        raise ValueError("only csv and json are read (parquet is not)")
    res = opendata.dataset_resource(dataset, fmt, title_contains, client)
    dest = Path(work_dir) / f"decp-{datetime.now(timezone.utc):%Y%m%d}.{fmt.lower()}"
    size = opendata.download(res["url"], dest, client)
    try:
        stats = run(dsn, str(dest), res["url"],
                    label or f"DECP consolidated {(res['last_modified'] or '')[:10]} ({res['dataset_title']})".replace(" ()", ""),
                    coverage_note or (f"data.gouv.fr dataset {res['dataset_page']}, resource '{res['title']}' modified {res['last_modified']}, "
                                      f"{size} bytes. DECP holds what buyers published: a company absent from it has NOT necessarily never worked for the public."),
                    rejects_path, mapping)
    finally:
        if not keep:
            dest.unlink(missing_ok=True)
    stats["downloaded_bytes"] = size
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="a DECP file you downloaded yourself (omit it with --download)")
    ap.add_argument("--download", action="store_true", help="fetch the newest consolidated DECP file from data.gouv.fr by itself")
    ap.add_argument("--dataset", help=f"data.gouv.fr dataset id (default env DECP_DATASET_ID, else {DEFAULT_DATASET})")
    ap.add_argument("--format", default="csv")
    ap.add_argument("--title-contains", help="pick the resource whose title contains this text")
    ap.add_argument("--if-older-than-days", type=int, help="with --download: skip when a DECP load younger than N days exists")
    ap.add_argument("--keep-file", action="store_true", help="keep the downloaded file in --work-dir")
    ap.add_argument("--work-dir", default="data/downloads")
    ap.add_argument("--source-url", help="dataset / resource URL the file comes from (the proof of every row); required with FILE")
    ap.add_argument("--label")
    ap.add_argument("--coverage-note")
    ap.add_argument("--rejects", default="data/exports/decp_rejects.csv")
    ap.add_argument("--map", action="append", default=[], metavar="FIELD=HEADER", help="CSV header override, e.g. holder_id=Titulaire_SIRET")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mapping = dict(m.split("=", 1) for m in a.map)
    if a.download:
        print(download_and_run(os.environ["DATABASE_URL"], a.dataset or os.environ.get("DECP_DATASET_ID", DEFAULT_DATASET), a.format,
                               a.title_contains, a.label, a.coverage_note, a.rejects, mapping, a.work_dir, a.keep_file,
                               a.if_older_than_days))
        return
    if not a.file or not a.source_url:
        ap.error("give FILE and --source-url, or use --download")
    print(run(os.environ["DATABASE_URL"], a.file, a.source_url, a.label, a.coverage_note, a.rejects, mapping))


if __name__ == "__main__":
    main()
