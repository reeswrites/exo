"""What the incremental consumption fetchers share.

`fetch-goodreads` does not use this: its feed returns the whole shelf, so it can
rewrite its cache from scratch every run. The three fetchers here cannot. Their
feeds are short windows — Letterboxd's RSS carries ~50 entries, Untappd's ~25,
and Last.fm's API pages back from a watermark — so the cache file *is* the
history, and everything below exists to keep that file from getting smaller.

Stdlib only, like every other puller in the engine: this runs on whatever Python
the machine has, and an HTTP dependency is not worth acquiring for four GETs.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from .. import config

USER_AGENT = "exo/1.0"
TIMEOUT = 30

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    """Feed descriptions are HTML fragments; the record wants the prose."""
    text = _TAGS.sub(" ", (s or ""))
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return _WS.sub(" ", text).strip()


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def local_tag(el) -> str:
    """An ElementTree tag without its namespace.

    Letterboxd's feed puts `watchedDate` and friends in its own namespace, and
    the URI is theirs to change. Matching on the local name means a namespace
    move degrades to nothing rather than to every entry silently failing the
    `has a watched date` test — which reads exactly like "he watched no films".
    """
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def children(el) -> dict[str, str]:
    """`{local tag: text}` for one item. Last sibling wins; feeds do not repeat."""
    return {local_tag(c): (c.text or "").strip() for c in el}


class CacheRefused(Exception):
    """Raised instead of writing a cache that lost entries."""


def read_cache(name: str, key: str) -> tuple[list[dict], dict]:
    """`(entries, whole file)` for one cache under the instance's exports dir.

    A missing or unreadable cache is an empty start, never an error — that is
    the first run. A cache that exists and *fails to parse* is different and is
    also an empty start here, but the caller's grow-only guard then refuses the
    write, so a corrupt file degrades to "nothing happened" rather than to a
    corpus of one day's feed.
    """
    path = config.EXPORTS / name
    if not path.exists():
        return [], {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return [], {}
    if not isinstance(blob, dict):
        return [], {}
    entries = blob.get(key)
    return (entries if isinstance(entries, list) else []), blob


def write_cache(name: str, key: str, entries: list[dict], *,
                had: int, extra: dict | None = None) -> Path:
    """Write the cache, or refuse.

    `had` is what the file held before this run. The feeds these caches are
    built from cannot re-serve their own history, so a run that ends with fewer
    entries than it started with has not found less — it has failed to parse
    what it fetched, or fetched a truncated response, and writing that result
    destroys history no upstream can give back. Refusing costs one stale day.
    """
    if len(entries) < had:
        raise CacheRefused(
            f"{name}: {len(entries):,} entries after merging, but the cache held "
            f"{had:,} before — refusing to write. These feeds cannot re-serve "
            f"their own history, so a shrink is data loss, not a smaller answer.")
    path = config.EXPORTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**(extra or {}), key: entries}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    return path


def report(label: str, new: int, total: int, path: Path, dates: list[str]) -> None:
    span = [d for d in sorted(dates) if d]
    print(f"  {label}: {new:,} new, {total:,} total -> {path.name}"
          + (f"  ({span[0]} .. {span[-1]})" if span else ""))


# ─────────────────────────────── the clock ───────────────────────────────


def resolve_tz(blob: dict, cache_name: str):
    """`(tzinfo | None, name)` — which clock this cache renders instants in.

    Three layers, and the first one is the important one:

      the cache's own `tz`   an existing file is never reinterpreted. The rows
                             already in it were rendered under some clock, and
                             re-rendering new rows under a different one puts two
                             clocks in a file the loader reads as one stream.
      config.TIMEZONE        what this instance declares. Setting it is how a
                             cache gets pinned for the first time.
      the runner's zone      what happened before any of this was nameable.
                             Recorded as "local", never as a zone name we would
                             be guessing at.

    `None` means "use the runner's zone", i.e. `datetime.astimezone()` with no
    argument — the pre-existing behaviour, preserved exactly.
    """
    declared = (blob.get("tz") or "").strip()
    name = declared or config.TIMEZONE.strip() or "local"
    if name == "local":
        print(f"  {cache_name}: rendering times in this machine's zone — set "
              f"[owner] timezone in exo.toml (or EXO_TZ) to pin it. Moving this "
              f"fetch to a runner in another zone will shift every new row.")
        return None, "local"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception as exc:
        # A bad zone name must not be resolved to "whatever is local" — that is
        # the mixed-clock cache this exists to prevent, arriving quietly.
        raise CacheRefused(
            f"{cache_name}: timezone {name!r} is not a zone this machine knows "
            f"({exc}). Fix it or clear it; guessing a clock corrupts the file.")
