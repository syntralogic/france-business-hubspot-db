"""Download an open-data file (data.gouv.fr dataset resource or a direct URL) to disk, streaming (files can be several GB).

Used by the bulk loaders (`fbdb.enrich.decp --download`, `fbdb.enrich.ademe_rge --download`) so nobody has to fetch the file by hand.
No key is needed. **NOT verified live** (the build sandbox cannot reach data.gouv.fr): the shape of the data.gouv.fr dataset API
(`GET /api/1/datasets/<id>/` -> `resources[]` with `url`, `format`, `title`, `type`, `last_modified`, `filesize`) is read from its public
documentation; when the shape differs the error lists the resources that WERE found, nothing is guessed.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx

API = os.environ.get("DATAGOUV_API_BASE", "https://www.data.gouv.fr/api/1")
UA = "fbdb/1.0 (open-data loader)"


class OpenDataError(RuntimeError):
    pass


def _client(client: Optional[httpx.Client]) -> httpx.Client:
    return client or httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True, headers={"User-Agent": UA})


def pick_resource(resources: list[dict], fmt: str = "csv", title_contains: Optional[str] = None) -> dict:
    """The newest MAIN resource of the wanted format (documentation / code resources are never picked)."""
    cands = []
    for r in resources or []:
        if (r.get("type") or "main") != "main" or not r.get("url"):
            continue
        if (r.get("format") or "").strip().lower() != fmt.lower():
            continue
        if title_contains and title_contains.lower() not in (r.get("title") or "").lower():
            continue
        cands.append(r)
    if not cands:
        seen = [f"{r.get('title')!s} [{r.get('format')!s}, {r.get('type')!s}]" for r in resources or []]
        raise OpenDataError(f"no main '{fmt}' resource" + (f" with '{title_contains}' in its title" if title_contains else "") +
                            f"; found: {seen} (use --format / --title-contains)")
    cands.sort(key=lambda r: r.get("last_modified") or r.get("created_at") or "", reverse=True)
    return cands[0]


def dataset_resource(dataset_id: str, fmt: str = "csv", title_contains: Optional[str] = None, client: Optional[httpx.Client] = None) -> dict:
    """{url, title, format, last_modified, filesize, dataset_title, dataset_page} of the resource to download."""
    c = _client(client)
    r = c.get(f"{API}/datasets/{dataset_id}/")
    r.raise_for_status()
    d = r.json()
    res = pick_resource(d.get("resources"), fmt, title_contains)
    return {"url": res["url"], "title": res.get("title"), "format": res.get("format"), "last_modified": res.get("last_modified"),
            "filesize": res.get("filesize"), "dataset_title": d.get("title") or dataset_id,
            "dataset_page": d.get("page") or f"https://www.data.gouv.fr/datasets/{dataset_id}"}


def download(url: str, dest: str | Path, client: Optional[httpx.Client] = None) -> int:
    """Stream `url` into `dest` (written as dest.part, renamed only when complete). Returns the byte count; an HTML page or an empty
    body is an error, never a file."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    n = 0
    try:
        with _client(client).stream("GET", url) as r:
            r.raise_for_status()
            if "text/html" in (r.headers.get("content-type") or "").lower():
                raise OpenDataError(f"{url} answered an HTML page, not a data file")
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    n += len(chunk)
        if n == 0:
            raise OpenDataError(f"{url} returned an empty body")
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    return n
