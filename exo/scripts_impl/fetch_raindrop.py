"""`exo fetch-raindrop` — the whole bookmark library from the Raindrop REST API.

Until now `t0_raindrop` came from a snapshot pulled once through the Raindrop
MCP, and nothing refreshed it: the record stopped at the day it was taken. The
MCP also does not return two fields the REST API does — the page's `excerpt`,
and the owner's own `note` on why a link was worth keeping — so the snapshot
carried titles alone, which is too little text to search by meaning.

This fetcher pages the API into `raindrop-cache.json` beside the other caches,
and the loader prefers that file to the snapshot. Same shape the loader already
reads (the snapshot's), so `_row` does not learn a second dialect.

The shrink guard is the Reading Log's, not the RSS caches' grow-only rule: this
is the whole library every time, and deleting a save is a legitimate edit. A
small drop is written; a large one (a revoked token, a truncated page walk) is
refused and the previous cache stays.

Needs RAINDROP_TOKEN: a test token from app.raindrop.io/settings/integrations.
It is read-only in practice (this file only GETs) but Raindrop does not offer a
read-only scope, so keep it a secret like the others.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import _fetch

CACHE = "raindrop-cache.json"
KEY = "bookmarks"
API = "https://api.raindrop.io/rest/v1"
PER_PAGE = 50            # the API's maximum
MAX_PAGES = 400          # 20,000 saves; a runaway walk stops rather than loops
MAX_DROP = 0.10


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{API}{path}", headers={
        "Authorization": f"Bearer {token}", "User-Agent": "exo-fetch"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed host)
                return json.load(r)
        except urllib.error.HTTPError as exc:
            # 429 is the documented rate limit (120/min); back off and retry.
            if exc.code == 429 and attempt < 2:
                time.sleep(int(exc.headers.get("Retry-After") or 30))
                continue
            raise
    raise RuntimeError("unreachable")


def collections(token: str) -> dict[str, str]:
    """Collection id -> title, nested ones included. `/collections` returns only
    the root level, so a save filed in a sub-collection would otherwise surface
    with a bare number where its bucket's name belongs."""
    out: dict[str, str] = {}
    for path in ("/collections", "/collections/childrens"):
        for c in _get(path, token).get("items", []):
            out[str(c["_id"])] = c.get("title", "")
    return out


def parse(item: dict) -> dict:
    """One API raindrop -> one cache entry, in the snapshot's field names."""
    return {
        "bookmark_id": item.get("_id"),
        "title": item.get("title") or "",
        "type": item.get("type") or "link",
        "collection_id": (item.get("collection") or {}).get("$id"),
        "created": item.get("created") or "",
        "link": item.get("link") or "",
        "tags": item.get("tags") or [],
        "note": (item.get("note") or "").strip(),
        "excerpt": (item.get("excerpt") or "").strip(),
    }


def fetch(token: str) -> tuple[list[dict], dict[str, str]]:
    cols = collections(token)
    out: list[dict] = []
    for page in range(MAX_PAGES):
        items = _get(f"/raindrops/0?perpage={PER_PAGE}&page={page}&sort=created",
                     token).get("items", [])
        if not items:
            break
        out.extend(parse(it) for it in items)
    else:
        raise RuntimeError(f"more than {MAX_PAGES} pages — refusing a walk that never ends")
    return out, cols


def run() -> int:
    token = os.environ.get("RAINDROP_TOKEN", "").strip()
    if not token:
        print("fetch-raindrop: set RAINDROP_TOKEN (a test token from Raindrop's integrations page)")
        return 1
    try:
        saves, cols = fetch(token)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        print(f"  fetch failed: {exc} — keeping the existing cache")
        return 1
    if not saves:
        print("fetch-raindrop: the API returned no saves — refusing to overwrite the cache")
        return 1

    had, _ = _fetch.read_cache(CACHE, KEY)
    floor = int(len(had) * (1 - MAX_DROP))
    if len(saves) < floor:
        print(f"fetch-raindrop: {len(saves):,} saves, but the cache held {len(had):,} "
              f"— more than {MAX_DROP:.0%} gone in one run; refusing to write")
        return 1

    path = _fetch.write_cache(CACHE, KEY, saves, had=0, extra={"collections": cols})
    noted = sum(1 for s in saves if s["note"])
    excerpted = sum(1 for s in saves if s["excerpt"])
    print(f"  wrote {len(saves):,} saves ({noted:,} with the owner's note, "
          f"{excerpted:,} with an excerpt, {len(cols)} collections) -> {path.name}")
    return 0
