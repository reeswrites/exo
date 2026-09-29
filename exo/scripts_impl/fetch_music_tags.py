"""`exo fetch-music-tags` — what the world calls the records he plays.

The scrobble stream says WHAT was played and never what it sounds like, so a
question like "something high-energy" had nothing on this surface to match
against — the assistant could only guess from the names, which it does well for
canonical records and badly for the long tail that is most of this listening.
Last.fm's crowd tags are the cheapest honest answer: `energetic`, `hardcore`,
`ambient`, `hyperpop` are other listeners' words about a record, public, and
already keyed on the same artist and album strings the scrobbles carry.

## Two levels, asked separately

`album.getTopTags` is precise and sparse: a record with a few hundred listeners
often has no tags at all. `artist.getTopTags` covers nearly everything and is
coarse — an ambient record by a loud artist gets the loud artist's tags. Both
are cached, as separate entries, and the choice between them is made at READ
time (the `albums` tool prefers the album's own and says which it used). Folding
them here would bake a fallback policy into the record and hide which claim a
row is actually making.

## Budgeted, most-played first

Roughly one call per album and one per artist — thousands on a first run, a
handful on a settled night. Each run spends at most `--budget` calls, in order of
plays, so the records that matter are answered first and a first fill spreads
over a few nights rather than one long pull. Tags drift slowly; an answer is
re-asked after `STALE_DAYS`, oldest first, only once nothing new is waiting.

## An empty answer is kept

"Last.fm has no tags for this" is an answer, and it is cached like one — with
`status: "none"` rather than as a missing key — so an obscure record is asked
once rather than every night, and a reader can tell "untagged" from "never
asked". A transport failure is not an answer: the run stops and keeps what it
has, because a string of timeouts written down as `none` would read as a
catalogue with no genre.

Stdlib only, like every other puller in the engine.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from collections import Counter
from datetime import date, timedelta

from . import _fetch

API = "https://ws.audioscrobbler.com/2.0/"
CACHE = "lastfm-tags-cache.json"
BUDGET = 800          # calls per run; ~4 minutes at the pace below
STALE_DAYS = 180
TOP_N = 8             # past the eighth, Last.fm's tags are one listener's
MIN_WEIGHT = 10       # its own 0-100 scale, relative to the record's top tag

# Tags that describe a listener's relationship to the record rather than the
# record. Dropped at fetch time because nothing downstream can use them and
# they would crowd real tags out of a TOP_N list.
NOISE = {
    "seen live", "favorites", "favourites", "favorite", "favourite",
    "albums i own", "my albums", "owned", "vinyl", "spotify", "check out",
    "love", "loved", "awesome", "amazing", "beautiful", "under 2000 listeners",
    "classic", "goat", "aoty", "peak", "masterpiece", "best", "favorite albums",
    "american", "british", "usa", "uk",
}
_YEAR = re.compile(r"^\d{4}$")   # "2025" is a release year, not a sound


def is_noise(tag: str, artist: str = "") -> bool:
    """True for a tag that says nothing about how a record sounds. Shared with
    the loader, so tightening the list cleans the existing cache on the next
    ingest instead of waiting STALE_DAYS for every record to be re-asked."""
    return (not tag or tag in NOISE or bool(_YEAR.match(tag))
            or tag == artist.strip().lower())


def key(artist: str, album: str = "") -> str:
    """The cache key, and the join key the worker matches on: lowercased and
    trimmed, nothing cleverer. Folding editions or punctuation here would make a
    tag row match scrobbles its album string never named."""
    return f"{artist.strip().lower()}\x1f{album.strip().lower()}"


def _clean(raw, artist: str) -> list[str]:
    tags = raw.get("tag", []) if isinstance(raw, dict) else []
    if isinstance(tags, dict):          # a one-tag response is not a list
        tags = [tags]
    out: list[str] = []
    for t in tags:
        name = " ".join(str(t.get("name", "")).lower().split())
        try:
            weight = int(t.get("count", 0))
        except (TypeError, ValueError):
            weight = 0
        # An artist tagged with their own name is a label, not a description.
        if is_noise(name, artist) or weight < MIN_WEIGHT:
            continue
        # A comma inside a tag would split it in two at the D1 boundary, where
        # the list travels comma-joined (publish_cf has no LIST type).
        name = name.replace(",", " ")
        if name not in out:
            out.append(name)
        if len(out) == TOP_N:
            break
    return out


def _ask(api_key: str, artist: str, album: str) -> tuple[str, list[str]]:
    """`(status, tags)` — `ok` or `none`. Raises on transport failure."""
    params = {"api_key": api_key, "format": "json", "artist": artist, "autocorrect": 1}
    if album:
        params |= {"method": "album.getTopTags", "album": album}
    else:
        params |= {"method": "artist.getTopTags"}
    body = json.loads(_fetch.get(f"{API}?{urllib.parse.urlencode(params)}"))
    if "error" in body:
        # 6 is "not found" — an answer. Anything else (rate limit, bad key,
        # outage) is the service failing, and must not be cached as "no tags".
        if body.get("error") == 6:
            return "none", []
        raise RuntimeError(f"last.fm error {body.get('error')}: {body.get('message')}")
    tags = _clean(body.get("toptags") or {}, artist)
    return ("ok" if tags else "none"), tags


def _wanted() -> list[tuple[str, str, int]]:
    """`(artist, album, plays)` for every record and every artist in the stream,
    most-played first. An album of "" is the artist-level entry."""
    from ..loaders import csv_sources

    albums: Counter = Counter()
    artists: Counter = Counter()
    spelling: dict[str, tuple[str, str]] = {}
    for r in csv_sources.lastfm():
        artist = (r.payload.get("artist") or "").strip()
        album = (r.payload.get("album") or "").strip()
        if not artist:
            continue
        artists[key(artist)] += 1
        spelling.setdefault(key(artist), (artist, ""))
        if album:
            albums[key(artist, album)] += 1
            spelling.setdefault(key(artist, album), (artist, album))
    both = list(albums.items()) + list(artists.items())
    both.sort(key=lambda kv: (-kv[1], kv[0]))
    return [(*spelling[k], n) for k, n in both]


def run(budget: int | None = None) -> int:
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        print("fetch-music-tags: set LASTFM_API_KEY")
        return 1
    budget = BUDGET if budget is None else max(0, budget)

    cached, _blob = _fetch.read_cache(CACHE, "entries")
    had = len(cached)
    by_key = {e["key"]: e for e in cached if isinstance(e, dict) and e.get("key")}

    wanted = _wanted()
    if not wanted:
        print("fetch-music-tags: no scrobbles loaded — nothing to ask about")
        return 0

    today = date.today()
    stale_before = (today - timedelta(days=STALE_DAYS)).isoformat()
    fresh = [w for w in wanted if key(w[0], w[1]) not in by_key]
    # Re-asks wait until nothing new is queued, and go oldest answer first.
    stale = sorted(
        (w for w in wanted if by_key.get(key(w[0], w[1]), {}).get("asked", "") < stale_before
         and key(w[0], w[1]) in by_key),
        key=lambda w: by_key[key(w[0], w[1])].get("asked", ""))
    queue = (fresh + stale)[:budget]

    asked = failed = 0
    for artist, album, plays in queue:
        try:
            status, tags = _ask(api_key, artist, album)
        except Exception as exc:
            failed += 1
            print(f"  {artist} / {album or '(artist)'} failed ({exc}) — stopping, keeping what we have")
            break
        by_key[key(artist, album)] = {
            "key": key(artist, album), "artist": artist, "album": album,
            "level": "album" if album else "artist",
            "status": status, "tags": tags, "asked": today.isoformat(),
        }
        asked += 1
        time.sleep(0.25)

    merged = sorted(by_key.values(), key=lambda e: e["key"])
    try:
        path = _fetch.write_cache(CACHE, "entries", merged, had=had)
    except _fetch.CacheRefused as exc:
        print(f"fetch-music-tags: {exc}")
        return 1

    tagged = sum(1 for e in merged if e.get("status") == "ok")
    waiting = max(0, len(fresh) - asked)
    print(f"  music-tags: {asked:,} asked, {len(merged):,} cached "
          f"({tagged:,} tagged, {len(merged) - tagged:,} untagged) -> {path.name}"
          + (f"; {waiting:,} never asked, next run" if waiting else ""))
    return 1 if failed and not asked else 0
