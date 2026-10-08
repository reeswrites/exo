"""T0 — the unheard pool: records that exist and have not been played.

Offline by contract: reads what `exo fetch-album-pool` cached, plus the scrobble
stream to know what has been heard. One row per candidate record, however many
lists named it — `found_via` says which (back_catalog, scene), `scenes` which
tags surfaced it, `rank` its best position on any list.

## Heard, matched loosely

The stream and Last.fm's catalogue spell the same record differently: "Cold
Visions" against "Cold Visions (Deluxe)", "WELCOME HOME - EP" against "Welcome
Home". An exact match would offer a record back to the person who played it
749 times, which is the one answer this pool must never give. So heard is
decided here on a loose key — bracketed suffixes, edition words and punctuation
stripped. The `unheard` tool trusts it and does not re-check against
`t0_music` at read time: D1's `t0_music` is written by the same nightly load as
this pool, so "played since the last ingest" is always empty there, and the
re-check — two aggregates over the whole stream, evaluated twice per call —
read ~4M rows a call, most of D1's free daily allowance.

## Plays, counted here

`artist_plays` is how much the stream already plays the act, counted on
`fold(artist)` — the same fold the worker's joins use — over every scrobble,
album or not. Counted at ingest for the same reason: it cannot change between
loads, and counting it per call cost the other half of those reads.

`grounds=False`, `author=external`: the pool is Last.fm's lists, not his record.
Held PRIVATE: which artists and scenes it was built from is a description of
his taste, the same reasoning `t0_release` is graded on.
"""
from __future__ import annotations

import re
from collections import Counter

from .. import config
from ..provenance import Row, stable_id
from ..scripts_impl.fetch_album_pool import CACHE
from ..scripts_impl.fetch_music_tags import is_noise, key
from .music_tags import fold

# Stored identity (ADR-0014 §7) — hashed into every row id. Do not rename.
SOURCE = "lastfm-pool"

_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_EDITION = re.compile(r"\s-\s(ep|single|deluxe.*|remaster.*|expanded.*)$")
_NON_ALNUM = re.compile(r"[^0-9a-zÀ-￿]+")


def loose(artist: str, album: str) -> str:
    """A matching key that forgives edition suffixes and punctuation. Only ever
    compared with itself — never stored as an identity."""
    a = album.lower().strip()
    a = _EDITION.sub("", _BRACKETS.sub("", a)).strip()
    return f"{_NON_ALNUM.sub('', artist.lower())}\x1f{_NON_ALNUM.sub('', a)}"


def _stream() -> tuple[set[str], Counter[str]]:
    """One pass over the scrobbles: the loose keys of every record heard, and
    the plays per folded artist."""
    from . import csv_sources
    heard: set[str] = set()
    plays: Counter[str] = Counter()
    for r in csv_sources.lastfm():
        artist = r.payload.get("artist") or ""
        album = r.payload.get("album") or ""
        plays[fold(artist)] += 1
        if album.strip():
            heard.add(loose(artist, album))
    return heard, plays


def load() -> list[Row]:
    from ..scripts_impl import _fetch

    entries, _ = _fetch.read_cache(CACHE, "entries")
    if not entries:
        if not (config.EXPORTS / CACHE).exists():
            print("  album_pool: no cache yet — run `exo fetch-album-pool`")
        return []

    tags = {e["key"]: e for e in entries if e.get("kind") == "tags"}
    heard, plays = _stream()
    pool: dict[str, dict] = {}
    for e in entries:
        kind = e.get("kind")
        if kind not in ("catalog", "scene"):
            continue
        via = "back_catalog" if kind == "catalog" else "scene"
        for a in e.get("albums") or []:
            if loose(a["artist"], a["album"]) in heard:
                continue
            k = key(a["artist"], a["album"])
            c = pool.setdefault(k, {"artist": a["artist"], "album": a["album"],
                                    "via": set(), "scenes": set(), "rank": None})
            c["via"].add(via)
            if kind == "scene":
                c["scenes"].add(e["name"])
            r = a.get("rank") or None
            if r and (c["rank"] is None or r < c["rank"]):
                c["rank"] = r

    rows = []
    for k in sorted(pool):
        c = pool[k]
        t = tags.get(k) or {}
        clean = [x for x in (t.get("tags") or []) if not is_noise(x, c["artist"])]
        rows.append(Row(
            tier="t0", zone="album_pool", source=SOURCE, author="external",
            grounds=False, origin_ref=CACHE,
            id=stable_id(SOURCE, k),
            payload={
                "artist": c["artist"],
                "album": c["album"],
                "artist_key": fold(c["artist"]),
                "album_key": fold(c["album"]),
                # Comma-joined, not lists: D1 takes scalars only.
                "found_via": ", ".join(sorted(c["via"])),
                "scenes": ", ".join(sorted(c["scenes"])),
                "rank": c["rank"],
                "artist_plays": plays.get(fold(c["artist"]), 0),
                # `pending` = not asked yet; `none` = asked, Last.fm has no tags.
                "tag_status": ("ok" if clean else "none") if t else "pending",
                "tags": ", ".join(clean),
            },
        ))
    return rows
