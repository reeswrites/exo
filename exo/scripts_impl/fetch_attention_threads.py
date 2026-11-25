"""`exo fetch-attention-threads` — what has been holding the owner's attention.

A Notion database kept by a chat-consolidation skill: one row per thread of
thinking that runs across chats and days, with how many chats touched it, when
it was first and last touched, a short summary of where it stands, and dated
one-line "moves". It replaces the chat-export feed `recent_topics` used to read,
which stopped when the exports did.

This file only fetches, into `attention-threads-cache.json`;
`loaders.attention` turns the cache into `t0_attention_thread`.

Expected properties (a missing one reads as empty, never an error):

    Thread        title
    Summary       text     where it stands, 1-3 sentences (machine-written)
    Moves         text     dated one-liners, newest last (machine-written)
    Touches       number   how many chat sessions touched it
    Load          select   Heavy | Medium | Light
    First seen    date
    Last touched  date
    Authorship    select   AI | Mixed

The database's own `Status` (Live / Cooling / Dormant) is deliberately not
copied. It means "touched in the last two hours", which a nightly copy turns
into a lie by morning; the surface derives recency from `Last touched` at the
moment it is asked instead.

The shrink guard is loose: threads get merged and pruned, and that is an edit.
Only a collapse (a revoked integration, a truncated query) is refused.
"""
from __future__ import annotations

import os

from ..notes.sources.notion import api
from . import _fetch

CACHE = "attention-threads-cache.json"
KEY = "threads"
MAX_DROP = 0.5


def _prop(page: dict, name: str) -> dict:
    return (page.get("properties") or {}).get(name) or {}


def _text(page: dict, name: str) -> str:
    return api._prop_text(_prop(page, name)).strip()


def _title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(p.get("plain_text", "") for p in prop.get("title") or []).strip()
    return ""


def _date(page: dict, name: str) -> str | None:
    value = _prop(page, name).get("date") or {}
    return (value.get("start") or "").strip() or None


def _select(page: dict, name: str) -> str:
    return ((_prop(page, name).get("select") or {}).get("name") or "").strip()


def parse(page: dict) -> dict | None:
    """One Notion page -> one cache entry, or None for an untitled page."""
    title = _title(page)
    if not title:
        return None
    n = _prop(page, "Touches").get("number")
    return {
        "page_id": page.get("id", ""),
        "title": title,
        "summary": _text(page, "Summary"),
        "moves": _text(page, "Moves"),
        "touches": int(n) if isinstance(n, (int, float)) else 0,
        "load": _select(page, "Load"),
        "first_seen": _date(page, "First seen"),
        "last_touched": _date(page, "Last touched"),
        "authorship": _select(page, "Authorship"),
        "edited": page.get("last_edited_time", ""),
    }


def run() -> int:
    database = os.environ.get("ATTENTION_THREADS_DATABASE_ID", "").strip()
    if not database:
        print("fetch-attention-threads: set ATTENTION_THREADS_DATABASE_ID")
        return 1
    try:
        pages = list(api._paged(f"/databases/{api._norm_id(database)}/query",
                                method="POST", body={}))
    except api.NotionError as exc:
        print(f"  fetch failed: {exc} — keeping the existing cache")
        return 1

    threads = [t for t in (parse(p) for p in pages if not p.get("archived")) if t]
    if not threads:
        print("fetch-attention-threads: the query returned no threads — refusing to overwrite the cache")
        return 1

    had, _ = _fetch.read_cache(CACHE, KEY)
    if len(threads) < int(len(had) * (1 - MAX_DROP)):
        print(f"fetch-attention-threads: {len(threads):,} threads, but the cache held "
              f"{len(had):,} — more than {MAX_DROP:.0%} gone in one run; refusing to write")
        return 1

    path = _fetch.write_cache(CACHE, KEY, threads, had=0)
    newest = max((t["last_touched"] or "" for t in threads), default="")
    print(f"  wrote {len(threads):,} threads, last touched {newest[:10] or '?'} -> {path.name}")
    return 0
