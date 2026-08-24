"""`exo fetch-lastfm` — pull scrobbles over the API into Exo-owned raw/.

`lastfm-cache.json` is what `csv_sources.lastfm()` splices onto the dated CSV
export to get from the export's last play to today. Until this existed, Exo
*read* that file and wrote nothing: it was produced by a fetcher in another
repository, on one machine, and CI asserted only that it was present. The
freshest copy of a listening history was gated on a laptop in a way no step in
the pipeline named (ADR-0015).

Incremental by watermark. `user.getRecentTracks` takes `from`, so a run asks
only for plays after the newest one already cached and pages forward. A settled
day costs one request.

The cache stays interchangeable with the one the blog's fetcher writes — same
keys, same `last_uts`, same `scrobbled_at` rendering — so either can resume from
the other's file. That is deliberate: two incremental caches over one upstream
converge, and neither should be authoritative over the other.

Stdlib only; Exo has no HTTP dependency and four GETs is not worth acquiring one.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import datetime

from .. import config
from . import _fetch

API = "https://ws.audioscrobbler.com/2.0/"
CACHE = "lastfm-cache.json"
PER_PAGE = 200
MAX_PAGES = 400  # 80,000 scrobbles in one run; a runaway backstop, not a limit

# The format `csv_sources.lastfm()` parses. It is a WALL TIME with no offset, so
# which wall clock it was rendered against is a fact about the file rather than
# about the play — see `_fetch.resolve_tz`.
TS = "%d %b %Y %H:%M"


def _page(user: str, api_key: str, page: int, since: int | None) -> dict:
    params = {
        "method": "user.getRecentTracks", "user": user, "api_key": api_key,
        "format": "json", "limit": PER_PAGE, "page": page,
    }
    if since is not None:
        params["from"] = since + 1
    raw = _fetch.get(f"{API}?{urllib.parse.urlencode(params)}")
    return json.loads(raw).get("recenttracks", {}) or {}


def run() -> int:
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    user = os.environ.get("LASTFM_USER", "").strip()
    if not api_key or not user:
        missing = [n for n, v in (("LASTFM_API_KEY", api_key), ("LASTFM_USER", user)) if not v]
        print(f"fetch-lastfm: set {', '.join(missing)}")
        return 1

    cached, blob = _fetch.read_cache(CACHE, "scrobbles")
    had = len(cached)
    try:
        tz, tz_name = _fetch.resolve_tz(blob, CACHE)
    except _fetch.CacheRefused as exc:
        print(f"fetch-lastfm: {exc}")
        return 1

    # The watermark is recomputed from the rows rather than trusted from the
    # file. A `last_uts` ahead of the newest row — a half-written cache, a hand
    # edit — would skip everything between the two, permanently and silently.
    seen = {r["uts"] for r in cached if isinstance(r.get("uts"), int)}
    since = max(seen) if seen else None

    new: list[dict] = []
    page = 1
    while page <= MAX_PAGES:
        try:
            recent = _page(user, api_key, page, since)
        except Exception as exc:
            print(f"  page {page} failed ({exc}) — keeping what we have")
            break

        tracks = recent.get("track", [])
        if isinstance(tracks, dict):   # a one-track response is not a list
            tracks = [tracks]
        if not tracks:
            break

        for t in tracks:
            # The now-playing entry has no date and is not a scrobble yet. It
            # would also come back on every run, since it has no uts to dedupe.
            if (t.get("@attr") or {}).get("nowplaying") == "true":
                continue
            date = t.get("date") or {}
            try:
                uts = int(date["uts"])
            except (KeyError, TypeError, ValueError):
                continue
            if uts in seen:
                continue
            seen.add(uts)
            stamp = datetime.fromtimestamp(uts, tz) if tz else datetime.fromtimestamp(uts)
            new.append({
                "artist": (t.get("artist") or {}).get("#text", ""),
                "album": (t.get("album") or {}).get("#text", ""),
                "song": t.get("name", ""),
                "scrobbled_at": stamp.strftime(TS),
                "uts": uts,
            })

        total = int((recent.get("@attr") or {}).get("totalPages", 1) or 1)
        if page >= total:
            break
        page += 1
        time.sleep(0.25)   # the API is not rate-limited hard; do not lean on it

    # Oldest first, so the file reads as the stream it is and the loader's
    # `max(created)` splice is over a sorted tail rather than an arbitrary one.
    merged = sorted(cached + new, key=lambda r: r.get("uts") or 0)
    try:
        path = _fetch.write_cache(
            CACHE, "scrobbles", merged, had=had,
            extra={"last_uts": max(seen) if seen else None, "tz": tz_name})
    except _fetch.CacheRefused as exc:
        print(f"fetch-lastfm: {exc}")
        return 1

    # ISO, not the cache's own "%d %b %Y" — `report` orders these as strings, and
    # day-first with a month NAME sorts by neither. It read "01 Apr 2023 .. 31 Oct
    # 2025" on a file whose newest scrobble was yesterday: 31 is the highest day
    # and Oct the highest month name that has one. The other two fetchers already
    # hand it ISO, which is why only this one lied.
    _fetch.report("lastfm", len(new), len(merged), path,
                  [datetime.fromtimestamp(r["uts"], tz).strftime("%Y-%m-%d")
                   if tz else datetime.fromtimestamp(r["uts"]).strftime("%Y-%m-%d")
                   for r in merged if r.get("uts")])
    return 0
