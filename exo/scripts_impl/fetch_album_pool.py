"""`exo fetch-album-pool` — records that exist and have not been played yet.

`albums` answers from what was played, so "a high-energy album I HAVEN'T heard"
had no candidates at all. This builds a pool of unheard records from two
sources, both Last.fm, both cached:

  back_catalog   `artist.getTopAlbums` for the most-played artists. The records
                 by acts already loved that never made it into the stream — the
                 cheapest, highest-hit source there is.
  scene          `tag.getTopAlbums` for the scenes the stream is densest in
                 (rage, digicore, screamo…). Chosen by the tag, never by
                 similarity to a played artist, so it can reach an act with no
                 plays at all — the thing a `getSimilar` walk structurally cannot
                 (instance ADR-0017).

Then `album.getTopTags` for each candidate not already in the stream, so an
unheard record is searchable with the same words as a heard one.

## Not `releases`

`t0_release` is what just came OUT, crawled from label rosters. This is what
EXISTS and was missed, at any age. Different question, different tool.

## Heard is decided at load

The pool keeps whatever the sources list. The loader drops records already in
the stream (matched loosely — "Cold Visions (Deluxe)" is Cold Visions). The
`unheard` tool does not re-check at read time: the served stream only changes
on the same nightly load, and the re-check cost most of D1's daily read quota.

## Budgeted like the tag fetcher

At most `--budget` calls a run, in priority order: back catalogs of the top
artists, then scenes, then candidate tags, then re-asks of lists older than
`LIST_STALE_DAYS` (catalogs and charts move; a new album should arrive).
Transport failure stops the run and keeps what it has.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from collections import Counter
from datetime import date, timedelta

from . import _fetch
from .fetch_music_tags import API, CACHE as TAG_CACHE, _clean, key

CACHE = "lastfm-pool-cache.json"
BUDGET = 800
TOP_ARTISTS = 200     # back catalogs asked for, most-played first
MIN_ARTIST_PLAYS = 20 # below this an artist is a passing listen, not an act loved
PER_ARTIST = 8        # an act's top eight covers its canon; the tail is compilations
TOP_SCENES = 30
PER_SCENE = 50
LIST_STALE_DAYS = 30
TAG_STALE_DAYS = 180

# Tags too broad to be a scene: `tag.getTopAlbums` for "rap" is the global rap
# chart, which is a popularity list, not a place this listening lives.
BROAD = {
    "rap", "hip-hop", "hip hop", "pop", "rock", "electronic", "indie",
    "alternative", "experimental", "instrumental", "beats", "lo-fi", "metal",
    "punk", "soul", "jazz", "folk", "dance", "female vocalists", "male vocalists",
    "singer-songwriter", "soundtrack", "classical", "electronica",
}
JUNK_TITLES = {"", "(null)", "null", "undefined", "[unknown]"}


def _get(params: dict) -> dict:
    return json.loads(_fetch.get(f"{API}?{urllib.parse.urlencode(params)}"))


def _catalog(api_key: str, artist: str) -> list[dict]:
    body = _get({"method": "artist.getTopAlbums", "artist": artist, "limit": PER_ARTIST,
                 "autocorrect": 1, "api_key": api_key, "format": "json"})
    if "error" in body:
        if body.get("error") == 6:
            return []
        raise RuntimeError(f"last.fm error {body.get('error')}: {body.get('message')}")
    albums = (body.get("topalbums") or {}).get("album") or []
    if isinstance(albums, dict):
        albums = [albums]
    out = []
    for i, a in enumerate(albums, 1):
        name = (a.get("name") or "").strip()
        if name.lower() in JUNK_TITLES:
            continue
        # The artist Last.fm answers with, not the one asked: autocorrect may
        # have fixed the spelling, and the album belongs to the corrected act.
        out.append({"artist": ((a.get("artist") or {}).get("name") or artist).strip(),
                    "album": name, "rank": i})
    return out


def _scene(api_key: str, tag: str) -> list[dict]:
    body = _get({"method": "tag.getTopAlbums", "tag": tag, "limit": PER_SCENE,
                 "api_key": api_key, "format": "json"})
    if "error" in body:
        if body.get("error") == 6:
            return []
        raise RuntimeError(f"last.fm error {body.get('error')}: {body.get('message')}")
    albums = (body.get("albums") or {}).get("album") or []
    if isinstance(albums, dict):
        albums = [albums]
    out = []
    for a in albums:
        name = (a.get("name") or "").strip()
        artist = ((a.get("artist") or {}).get("name") or "").strip()
        if name.lower() in JUNK_TITLES or not artist:
            continue
        try:
            rank = int((a.get("@attr") or {}).get("rank") or 0)
        except ValueError:
            rank = 0
        out.append({"artist": artist, "album": name, "rank": rank})
    return out


def _album_tags(api_key: str, artist: str, album: str) -> tuple[str, list[str]]:
    body = _get({"method": "album.getTopTags", "artist": artist, "album": album,
                 "autocorrect": 1, "api_key": api_key, "format": "json"})
    if "error" in body:
        if body.get("error") == 6:
            return "none", []
        raise RuntimeError(f"last.fm error {body.get('error')}: {body.get('message')}")
    tags = _clean(body.get("toptags") or {}, artist)
    return ("ok" if tags else "none"), tags


def _stream() -> tuple[list[tuple[str, int]], set[str]]:
    """`([(artist, plays)] most-played first, {album keys in the stream})`."""
    from ..loaders import csv_sources

    plays: Counter = Counter()
    spelling: dict[str, str] = {}
    heard: set[str] = set()
    for r in csv_sources.lastfm():
        artist = (r.payload.get("artist") or "").strip()
        if not artist:
            continue
        plays[key(artist)] += 1
        spelling.setdefault(key(artist), artist)
        album = (r.payload.get("album") or "").strip()
        if album:
            heard.add(key(artist, album))
    top = sorted(plays.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(spelling[k], n) for k, n in top], heard


def _scenes() -> list[str]:
    """The scenes the stream is densest in, by how many of its records carry the
    tag — read from the tag fetcher's cache, so this costs no calls."""
    entries, _ = _fetch.read_cache(TAG_CACHE, "entries")
    count: Counter = Counter()
    for e in entries:
        if e.get("level") == "album" and e.get("status") == "ok":
            for t in e.get("tags") or []:
                if t not in BROAD:
                    count[t] += 1
    return [t for t, _n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SCENES]]


def run(budget: int | None = None) -> int:
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        print("fetch-album-pool: set LASTFM_API_KEY")
        return 1
    budget = BUDGET if budget is None else max(0, budget)

    cached, _ = _fetch.read_cache(CACHE, "entries")
    had = len(cached)
    by_key = {e["key"]: e for e in cached if isinstance(e, dict) and e.get("key")}

    artists, heard = _stream()
    if not artists:
        print("fetch-album-pool: no scrobbles loaded — nothing to build a pool from")
        return 0
    loved = [a for a, n in artists if n >= MIN_ARTIST_PLAYS][:TOP_ARTISTS]
    scenes = _scenes()
    if not scenes:
        print("  album-pool: no tagged records yet — run fetch-music-tags first; "
              "back catalogs only this run")

    today = date.today()
    list_stale = (today - timedelta(days=LIST_STALE_DAYS)).isoformat()
    tag_stale = (today - timedelta(days=TAG_STALE_DAYS)).isoformat()

    def list_key(kind: str, name: str) -> str:
        return f"{kind}\x1f{name.strip().lower()}"

    # Candidates the lists already name, for the tag queue. Recomputed from the
    # cache every run so a list fetched this run feeds this run's tag calls.
    def candidates() -> list[tuple[str, str]]:
        seen: dict[str, tuple[str, str]] = {}
        for e in by_key.values():
            if e.get("kind") not in ("catalog", "scene"):
                continue
            for a in e.get("albums") or []:
                k = key(a["artist"], a["album"])
                if k not in heard:
                    seen.setdefault(k, (a["artist"], a["album"]))
        return list(seen.values())

    def queue():
        # Priority, fresh before stale within each: catalogs, scenes, tags.
        for a in loved:
            if list_key("catalog", a) not in by_key:
                yield ("catalog", a, "")
        for t in scenes:
            if list_key("scene", t) not in by_key:
                yield ("scene", t, "")
        for artist, album in candidates():
            if key(artist, album) not in by_key:
                yield ("tags", artist, album)
        stale = [e for e in by_key.values()
                 if e.get("asked", "") < (tag_stale if e.get("kind") == "tags" else list_stale)]
        for e in sorted(stale, key=lambda e: e.get("asked", "")):
            yield (e["kind"], e["name"], e.get("album", ""))

    asked = failed = 0
    done: set[tuple] = set()
    for kind, name, album in queue():
        if asked >= budget:
            break
        if (kind, name, album) in done:
            continue
        done.add((kind, name, album))
        try:
            if kind == "catalog":
                by_key[list_key(kind, name)] = {
                    "key": list_key(kind, name), "kind": kind, "name": name,
                    "albums": _catalog(api_key, name), "asked": today.isoformat()}
            elif kind == "scene":
                by_key[list_key(kind, name)] = {
                    "key": list_key(kind, name), "kind": kind, "name": name,
                    "albums": _scene(api_key, name), "asked": today.isoformat()}
            else:
                status, tags = _album_tags(api_key, name, album)
                by_key[key(name, album)] = {
                    "key": key(name, album), "kind": "tags", "name": name, "album": album,
                    "status": status, "tags": tags, "asked": today.isoformat()}
        except Exception as exc:
            failed += 1
            print(f"  {kind} {name} {album} failed ({exc}) — stopping, keeping what we have")
            break
        asked += 1
        time.sleep(0.25)

    merged = sorted(by_key.values(), key=lambda e: e["key"])
    try:
        path = _fetch.write_cache(CACHE, "entries", merged, had=had)
    except _fetch.CacheRefused as exc:
        print(f"fetch-album-pool: {exc}")
        return 1

    kinds = Counter(e.get("kind") for e in merged)
    pool = candidates()
    untagged = sum(1 for a, b in pool if key(a, b) not in by_key)
    print(f"  album-pool: {asked:,} asked; {kinds['catalog']:,} catalogs, "
          f"{kinds['scene']:,} scenes, {len(pool):,} unheard candidates "
          f"({untagged:,} awaiting tags) -> {path.name}")
    return 1 if failed and not asked else 0
