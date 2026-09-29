"""T0 — music tags: what other listeners call the records in the stream.

Offline by contract. It reads what `exo fetch-music-tags` cached and nothing
else, so `exo ingest` is reproducible with the wire unplugged.

One row per cache entry, at whichever level it was asked: an album entry
carries that record's own tags, an artist entry (album "") carries the artist's.
The fallback between the two is the read side's decision (the `albums` tool) and
is deliberately not made here — see `scripts_impl/fetch_music_tags.py`.

`grounds=False`: a tag is the world's claim about a record, not his about
himself, and derivation must not read other people's genre words as evidence
about him. `author=external` because the words are Last.fm listeners', not a
machine's conclusion.
"""
from __future__ import annotations

from .. import config
from ..provenance import Row, stable_id
from ..scripts_impl.fetch_music_tags import CACHE, is_noise

# Stored identity (ADR-0014 §7) — hashed into every row id. Do not rename.
SOURCE = "lastfm-tags"


def fold(s: str) -> str:
    """The join key, folded exactly as the worker folds a scrobble's strings:
    SQLite's `lower(trim(x))`, which lowercases ASCII only and trims spaces only.
    Python's own `.lower().strip()` would fold `Ö` and tabs that SQLite leaves
    alone, and every non-ASCII artist would silently miss its tags."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s.strip(" "))


def load() -> list[Row]:
    from ..scripts_impl import _fetch

    entries, _ = _fetch.read_cache(CACHE, "entries")
    if not entries:
        if not (config.EXPORTS / CACHE).exists():
            print("  music_tag: no cache yet — run `exo fetch-music-tags`")
        return []
    rows = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("artist"):
            continue
        album = e.get("album") or ""
        tags = [t for t in (e.get("tags") or []) if not is_noise(t, e["artist"])]
        status = "ok" if tags else "none"
        rows.append(Row(
            tier="t0", zone="music_tag", source=SOURCE, author="external",
            grounds=False,
            created=e.get("asked") or None,
            origin_ref=CACHE,
            id=stable_id(SOURCE, e["artist"].strip().lower(), album.strip().lower()),
            payload={
                "artist": e["artist"],
                "album": album,
                "artist_key": fold(e["artist"]),
                "album_key": fold(album),
                "level": "album" if album else "artist",
                # `none` is asked-and-answered-no, kept distinct from a record
                # that has no row at all (never asked).
                "status": status,
                # Comma-joined, not a list: the projection only survives to D1
                # as scalars (publish_cf's type map has no LIST).
                "tags": ", ".join(tags),
            },
        ))
    return rows
