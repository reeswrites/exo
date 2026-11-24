"""`exo fetch-reading-log` — books from the Notion Reading Log.

Goodreads' RSS shelves went dark (404 on every shelf, 2026-11), so books moved to
a Notion database the owner keeps by hand: seeded once from Goodreads, logged
directly from then on. This fetcher reads it through the same Notion client
`ingest-notes` uses and writes `reading-log-cache.json` beside the other caches.

Identity is the whole design question. `Row.id` hashes the payload, so a book
that comes back from Notion with its date spelled `2024-01-05` where the Goodreads
CSV said `2024-01-05T00:00:00` would re-mint, and the surface would announce a
life's reading as "recently added". So this file only *fetches*; the loader
(`csv_sources.goodreads`) overlays Notion onto the Goodreads row it already has
and keeps that row's spelling of every field whose meaning did not change. See
`overlay` below — it lives here because it is the contract between the two.

Rows carrying a `Goodreads ID` keep `source = "goodreads"` and that id as
`origin_ref`, exactly as before. Rows logged in Notion have no Goodreads id and
get `source = "reading-log"`, keyed on the Notion page id.

The shrink guard is looser than the RSS caches' grow-only rule, on purpose. Those
feeds cannot re-serve history, so any shrink is loss. This one is the whole
database every time, and deleting a row in Notion is a legitimate edit — so a
small drop is written, and only a large one (a disconnected integration, a
truncated query) is refused.
"""
from __future__ import annotations

import os

from ..notes.sources.notion import api
from . import _fetch

CACHE = "reading-log-cache.json"
KEY = "books"
# A run may drop at most this share of the rows the cache held. Deleting a few
# books in Notion is an edit; losing a tenth of the shelf in one night is not.
MAX_DROP = 0.10

# Notion's Status -> the Goodreads exclusive-shelf vocabulary the loader and the
# surface already speak. "Partly read" has no Goodreads equivalent and keeps a
# shelf of its own rather than being folded into one that would misdescribe it.
SHELF = {
    "Read": "read",
    "Reading": "currently-reading",
    "To read": "to-read",
    "Partly read": "partly-read",
}


def _prop(page: dict, name: str) -> dict:
    return (page.get("properties") or {}).get(name) or {}


def _text(page: dict, name: str) -> str:
    return api._prop_text(_prop(page, name)).strip()


def _title(page: dict, name: str = "Title") -> str:
    parts = _prop(page, name).get("title") or []
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _date(page: dict, name: str) -> str | None:
    value = _prop(page, name).get("date") or {}
    start = (value.get("start") or "").strip()
    return start or None


def _rating(page: dict) -> str:
    n = _prop(page, "Rating").get("number")
    # Goodreads writes 0 for unrated; keep that spelling so unrated books keep
    # their ids.
    return str(int(n)) if isinstance(n, (int, float)) and n else "0"


def parse(page: dict) -> dict | None:
    """One Notion page -> one cache entry, or None for a page with no title."""
    title = _title(page)
    if not title:
        return None
    status = _text(page, "Status")
    return {
        "page_id": page.get("id", ""),
        "goodreads_id": _text(page, "Goodreads ID"),
        "title": title,
        "book_author": _text(page, "Author"),
        "my_rating": _rating(page),
        "shelf": SHELF.get(status, status.lower().replace(" ", "-")),
        "date_read": _date(page, "Date read"),
        "date_added": _date(page, "Date added"),
        "notes": _text(page, "Notes"),
        "edited": page.get("last_edited_time", ""),
    }


def _same_date(a: str | None, b: str | None) -> bool:
    return (a or "")[:10] == (b or "")[:10]


def overlay(base: dict, entry: dict) -> dict:
    """Notion's values onto an existing Goodreads payload, keeping the base's
    spelling wherever the meaning is unchanged — so an unedited book hashes to
    the id it already has, and only a real edit re-mints.

    `base` is the loader payload (title, book_author, my_rating, avg_rating,
    shelf, date_read, date_added). Fields Notion does not carry (avg_rating)
    are kept from the base.
    """
    out = dict(base)
    for k in ("title", "book_author"):
        if (entry.get(k) or "").strip() != (base.get(k) or "").strip():
            out[k] = entry.get(k) or ""
    if str(int(float(base.get("my_rating") or 0))) != entry["my_rating"]:
        out["my_rating"] = entry["my_rating"]
    if entry.get("shelf") and entry["shelf"] != base.get("shelf"):
        out["shelf"] = entry["shelf"]
    for k in ("date_read", "date_added"):
        if not _same_date(base.get(k), entry.get(k)):
            out[k] = entry.get(k)
    return out


def run() -> int:
    database = os.environ.get("READING_LOG_DATABASE_ID", "").strip()
    if not database:
        print("fetch-reading-log: set READING_LOG_DATABASE_ID (the Reading Log database id)")
        return 1
    try:
        pages = list(api._paged(f"/databases/{api._norm_id(database)}/query",
                                method="POST", body={}))
    except api.NotionError as exc:
        print(f"  fetch failed: {exc} — keeping the existing cache")
        return 1

    books = [b for b in (parse(p) for p in pages if not p.get("archived")) if b]
    if not books:
        print("fetch-reading-log: the query returned no books — refusing to overwrite the cache")
        return 1

    had, _ = _fetch.read_cache(CACHE, KEY)
    floor = int(len(had) * (1 - MAX_DROP))
    if len(books) < floor:
        print(f"fetch-reading-log: {len(books):,} books, but the cache held {len(had):,} "
              f"— more than {MAX_DROP:.0%} gone in one run; refusing to write")
        return 1

    path = _fetch.write_cache(CACHE, KEY, books, had=0)
    by_shelf: dict[str, int] = {}
    for b in books:
        by_shelf[b["shelf"]] = by_shelf.get(b["shelf"], 0) + 1
    for shelf, n in sorted(by_shelf.items(), key=lambda kv: -kv[1]):
        print(f"  {shelf:<18}{n:>6,}")
    logged = sum(1 for b in books if not b["goodreads_id"])
    print(f"  wrote {len(books):,} books ({logged:,} logged in Notion) -> {path.name}")
    return 0
