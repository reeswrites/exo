"""`wh fetch-goodreads` — pull the shelves over RSS into Exo-owned raw/.

The Goodreads CSV is a hand-made export and stopped at 2026-03-25; every other
consumption source now reads an RSS/API cache alongside its CSV and runs months
ahead. This writes the cache Goodreads never had, in the same shape as the
letterboxd/untappd/lastfm ones so `csv_sources.goodreads()` can union it the same
way.

Deliberately a fetch step rather than a loader: it is the only consumption source
that needs the network at ingest time. Writing to raw/ means the result ships in
the CI tarball, so the nightly rebuild stays offline and needs no new secret.

Public shelves need no API key — only GOODREADS_USER_ID. Stdlib only; Exo
has no HTTP dependency and this is not worth acquiring one for.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import config

SHELVES = ("read", "currently-reading", "to-read")
PER_PAGE = 100
MAX_PAGES = 60  # 6,000 books; a runaway-loop backstop, not an expected limit

_TAGS = re.compile(r"<[^>]+>")


def _clean(s: str | None) -> str:
    return _TAGS.sub("", (s or "")).strip()


def _iso(raw: str) -> str | None:
    """Goodreads RSS dates are RFC 822: 'Sat, 25 Jul 2026 00:00:00 +0000'.

    Parsed with email.utils rather than a strptime format list — hand-rolled
    formats got the comma and the trailing offset wrong and silently returned
    None for all 881 books, which reads exactly like "this feed has no dates".
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return None


def _fetch(user_id: str, shelf: str, page: int) -> list[dict]:
    qs = urllib.parse.urlencode(
        {"shelf": shelf, "per_page": PER_PAGE, "page": page, "sort": "date_added"}
    )
    url = f"https://www.goodreads.com/review/list_rss/{user_id}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "exo/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())

    out: list[dict] = []
    for item in root.iterfind(".//item"):
        g = lambda tag: (item.findtext(tag) or "").strip()  # noqa: E731
        out.append({
            "book_id": g("book_id"),
            "title": _clean(g("title")),
            "book_author": _clean(g("author_name")),
            "my_rating": g("user_rating") or "0",
            "avg_rating": g("average_rating"),
            "shelf": shelf,
            "date_read": _iso(g("user_read_at")),
            "date_added": _iso(g("user_date_added")),
        })
    return out


def run() -> int:
    user_id = os.environ.get("GOODREADS_USER_ID", "").strip()
    if not user_id:
        print("fetch-goodreads: set GOODREADS_USER_ID (public shelves need no key)")
        return 1

    books: list[dict] = []
    seen: set[str] = set()
    for shelf in SHELVES:
        got = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                batch = _fetch(user_id, shelf, page)
            except Exception as exc:
                print(f"  {shelf}: page {page} failed ({exc}) — keeping what we have")
                break
            if not batch:
                break
            for b in batch:
                # A book can sit on several shelves; first shelf seen wins, and
                # SHELVES is ordered so 'read' beats 'to-read'.
                key = b["book_id"] or f'{b["title"]}|{b["book_author"]}'
                if key in seen:
                    continue
                seen.add(key)
                books.append(b)
                got += 1
            if len(batch) < PER_PAGE:
                break
        print(f"  {shelf:<18}{got:>6,}")

    if not books:
        print("fetch-goodreads: nothing fetched — refusing to overwrite the cache")
        return 1

    out = config.EXPORTS / "goodreads-cache.json"
    out.write_text(json.dumps({"books": books}, indent=1), encoding="utf-8")
    dated = sorted(b["date_read"] or b["date_added"] or "" for b in books)
    dated = [d for d in dated if d]
    print(f"  wrote {len(books):,} books -> {out.name}"
          + (f"  ({dated[0]} .. {dated[-1]})" if dated else ""))
    return 0
