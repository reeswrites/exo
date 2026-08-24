"""`exo fetch-untappd` — pull beer check-ins over RSS into Exo-owned raw/.

`untappd-cache.json` is what `csv_sources.untappd()` merges onto the dated CSV
export. Exo read it and wrote nothing until this existed (ADR-0015).

**The narrowest window of the three.** Untappd's RSS carries about twenty-five
check-ins. At any real drinking pace that is days, not months, so this cache is
the only record of everything between the last CSV export and the last time this
ran — and `_fetch.write_cache` refuses any write that would shrink it.

What the feed gives is thinner than the CSV: a title to parse, a comment, a
timestamp and a link. The native Untappd columns `csv_sources.untappd()` keeps
(`beer_type`, `rating_score`, `venue_lat`, `brewery_city`, …) are export-only,
so a cache-sourced check-in carries "" for them. That is honest — the loader
reads every column through `.get(c, "")` — and it means the CSV is still worth
re-exporting occasionally for the columns the feed cannot give.

Stdlib only, and the title is parsed with a regex because the feed states the
beer and the brewery in one sentence and nowhere else.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from . import _fetch

CACHE = "untappd-cache.json"

# "<user> is drinking a <beer> by <brewery> at <venue>". The venue clause is
# optional; the article is 'a' or 'an' and is not part of the beer's name.
_TITLE = re.compile(r".*? is drinking (?:an? )?(.+?) by (.+?)(?:\s+at\s+(.+))?$")

# What csv_sources.untappd() reads out of a cached check-in. Named here so a
# feed change that stops producing one of them is visible in this file rather
# than as an empty column three stages downstream.
FIELDS = ("beer_name", "brewery_name", "comment", "created_at",
          "checkin_url", "checkin_id", "venue_name")


def _title(text: str) -> tuple[str, str, str]:
    m = _TITLE.match(text or "")
    if not m:
        # Not a parse failure worth dropping the check-in over: the timestamp
        # and the link are still true. Keep it with the raw title as the beer.
        return (text or "").strip(), "", ""
    return m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()


def _created(pubdate: str, tz) -> str:
    """RFC-2822 pubDate -> 'YYYY-MM-DD HH:MM:SS' in the cache's own clock.

    The feed's offset is authoritative for the instant; the rendering is a wall
    time with no offset, so which clock it was rendered against is a fact about
    the file (`_fetch.resolve_tz`) and not about the check-in.
    """
    try:
        dt = parsedate_to_datetime((pubdate or "").strip())
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return (dt.astimezone(tz) if tz else dt.astimezone()).strftime("%Y-%m-%d %H:%M:%S")


def _entries(xml: bytes, tz) -> list[dict]:
    root = ET.fromstring(xml)
    out: list[dict] = []
    for item in root.iterfind(".//item"):
        f = _fetch.children(item)
        link = f.get("link", "")
        checkin_id = link.rstrip("/").rsplit("/", 1)[-1] if link else ""
        if not checkin_id:
            continue
        beer, brewery, venue = _title(f.get("title", ""))
        entry = {
            "beer_name": beer,
            "brewery_name": brewery,
            "comment": _fetch.strip_html(f.get("description", "")),
            "created_at": _created(f.get("pubDate", ""), tz),
            "checkin_url": link,
            "checkin_id": checkin_id,
            "_checkin_id": checkin_id,
        }
        if venue:
            entry["venue_name"] = venue
        out.append(entry)
    return out


def run() -> int:
    user = os.environ.get("UNTAPPD_USER", "").strip()
    key = os.environ.get("UNTAPPD_RSS_KEY", "").strip()
    if not user or not key:
        missing = [n for n, v in (("UNTAPPD_USER", user), ("UNTAPPD_RSS_KEY", key)) if not v]
        print(f"fetch-untappd: set {', '.join(missing)}")
        return 1

    cached, blob = _fetch.read_cache(CACHE, "checkins")
    had = len(cached)
    try:
        tz, tz_name = _fetch.resolve_tz(blob, CACHE)
    except _fetch.CacheRefused as exc:
        print(f"fetch-untappd: {exc}")
        return 1

    # Both spellings: the blog's fetcher writes `_checkin_id`, the CSV seed
    # writes `checkin_id`, and a cache may hold rows from either road.
    seen = {e.get("_checkin_id") or e.get("checkin_id") for e in cached}
    seen.discard(None)
    seen.discard("")

    try:
        fetched = _entries(_fetch.get(f"https://untappd.com/rss/user/{user}?key={key}"), tz)
    except Exception as exc:
        print(f"fetch-untappd: feed read failed ({exc}) — cache left as it was")
        return 1

    if not fetched:
        print("fetch-untappd: the feed parsed to zero check-ins — treating that "
              "as a markup change or a bad key, not an empty history")
        return 1

    new = [e for e in fetched if e["_checkin_id"] not in seen]
    merged = sorted(cached + new, key=lambda e: e.get("created_at") or "")
    try:
        path = _fetch.write_cache(CACHE, "checkins", merged, had=had, extra={"tz": tz_name})
    except _fetch.CacheRefused as exc:
        print(f"fetch-untappd: {exc}")
        return 1

    _fetch.report("untappd", len(new), len(merged), path,
                  [e.get("created_at", "")[:10] for e in merged])
    return 0
