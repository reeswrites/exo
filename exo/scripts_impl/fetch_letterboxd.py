"""`exo fetch-letterboxd` — pull the film diary over RSS into Exo-owned raw/.

`letterboxd-cache.json` is what `csv_sources.letterboxd()` merges onto the dated
ratings export. Exo read it and wrote nothing until this existed (ADR-0015).

**The feed is a window, not an archive.** Letterboxd's RSS carries roughly the
last fifty diary entries and there is no paging behind it. So this cache cannot
be rebuilt from the feed: everything older than the window lives in the file and
in the CSV export, and nowhere else. `_fetch.write_cache` refuses a write that
would shrink it for that reason — a shrink here is not a smaller answer, it is
history no upstream can hand back.

The entry shape matches the one the blog's fetcher writes, `_guid` included, so
the two caches are interchangeable and either can resume from the other's file.

Stdlib only: `xml.etree` with namespace-insensitive tag matching, because the
`letterboxd:` namespace URI is theirs to change and a hard-coded one would turn
a namespace move into "he watched no films" rather than into an error.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from . import _fetch

CACHE = "letterboxd-cache.json"

# Letterboxd writes this into the description of an entry with no review text.
# Keeping it would put boilerplate in front of the atomizer on every film.
_NO_REVIEW = "Watched on "


def _entries(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    out: list[dict] = []
    for item in root.iterfind(".//item"):
        f = _fetch.children(item)

        # Only diary entries carry a watched date. Lists, the watchlist and
        # profile updates ride the same feed and are not films he watched.
        watched = f.get("watchedDate")
        if not watched:
            continue

        guid = f.get("guid") or ""
        if not guid:
            guid = f"{f.get('filmTitle', '')}|{f.get('filmYear', '')}|{watched}"

        review = _fetch.strip_html(f.get("description", ""))
        if review.startswith(_NO_REVIEW):
            review = ""

        entry = {
            "date": watched,
            "name": f.get("filmTitle", ""),
            "year": f.get("filmYear", ""),
            "letterboxd_uri": f.get("link", ""),
            "_guid": guid,
        }
        # Written only when present, so an unrated watch is absent rather than
        # rated zero — `t0_film` carries the rating as a string and "" and "0"
        # are different claims.
        if f.get("memberRating"):
            entry["rating"] = f["memberRating"]
        if review:
            entry["review"] = review
        out.append(entry)
    return out


def run() -> int:
    user = os.environ.get("LETTERBOXD_USER", "").strip()
    if not user:
        print("fetch-letterboxd: set LETTERBOXD_USER (a public diary needs no key)")
        return 1

    cached, blob = _fetch.read_cache(CACHE, "watched")
    had = len(cached)
    seen = {e["_guid"] for e in cached if e.get("_guid")}

    try:
        fetched = _entries(_fetch.get(f"https://letterboxd.com/{user}/rss/"))
    except Exception as exc:
        print(f"fetch-letterboxd: feed read failed ({exc}) — cache left as it was")
        return 1

    if not fetched:
        # An empty parse of a feed that should always carry ~50 entries is a
        # markup change, not a quiet month. Say so rather than write nothing
        # and report success.
        print("fetch-letterboxd: the feed parsed to zero diary entries — "
              "treating that as a markup change, not an empty diary")
        return 1

    new = [e for e in fetched if e["_guid"] not in seen]
    merged = cached + new
    try:
        path = _fetch.write_cache(CACHE, "watched", merged, had=had,
                                  extra={k: v for k, v in blob.items() if k != "watched"})
    except _fetch.CacheRefused as exc:
        print(f"fetch-letterboxd: {exc}")
        return 1

    _fetch.report("letterboxd", len(new), len(merged), path,
                  [e.get("date", "") for e in merged])
    return 0
