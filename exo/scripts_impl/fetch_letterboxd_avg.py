"""`exo fetch-letterboxd-avg` — the community average per film, into Exo-owned raw/.

Distinct from `fetch_letterboxd`, which pulls his own diary over RSS. This pulls
what *everyone else* thought, and it exists for one reason: without a public
consensus to compare against, a taste model cannot be told apart from a quality
model. Goodreads' export ships `Average Rating` beside `My Rating` and that one
column is what let the book verticals be checked at all; Letterboxd's export has
no equivalent, so it is fetched.

Each film page embeds JSON-LD with an `aggregateRating` on the same 0.5-5 scale
as his own ratings, and needs no API key.

**Resumable, because this one is per-film rather than per-feed.** Six hundred
films is six hundred requests, so the cache is read first and only unseen films
are fetched, with a checkpoint every 25. An interrupted run costs nothing and a
rerun after a fresh export fetches only what is new. The grow-only guard in
`_fetch.write_cache` applies here as everywhere: a shrink is a parse failure,
not a smaller answer.
"""
from __future__ import annotations

import csv
import json
import re
import time

from .. import config
from . import _fetch

CACHE = "letterboxd-avg-cache.json"
KEY = "films"
DELAY = 0.7          # spacing between requests; this is one page per film
CHECKPOINT = 25

_LD = re.compile(rb'<script type="application/ld\+json">(.*?)</script>', re.S)
# Letterboxd wraps its JSON-LD in CDATA comment markers, which json cannot read.
_CDATA = re.compile(r"^\s*/\*.*?\*/|/\*.*?\*/\s*$", re.S)


def _key(title: str, year) -> str:
    """The same title+year identity `csv_sources.letterboxd()` merges on.

    Deliberately not the URI. Letterboxd hands out a different permalink per
    context, which is what inflated the film library by 116 phantom rows once
    already — keying the cache the way the loader keys its rows means the join
    is a dict lookup that cannot silently half-match.
    """
    return f"{(title or '').strip().lower()}|{str(year or '').strip()}"


def _average(uri: str) -> float | None:
    """The community average from a film page, or None if the page has none."""
    m = _LD.search(_fetch.get(uri))
    if not m:
        return None
    try:
        data = json.loads(_CDATA.sub("", m.group(1).decode("utf-8", "replace")))
    except json.JSONDecodeError:
        return None
    value = (data.get("aggregateRating") or {}).get("ratingValue")
    return float(value) if value is not None else None


def _films() -> list[tuple[str, str, str]]:
    """(title, year, uri) from the ratings CSV plus the diary cache, deduped.

    Both sources, because they disagree: the CSV is a dated export and the cache
    is the RSS window ahead of it. This mirrors what the loader unions, so the
    fetch covers exactly the films that will end up in `t0_film`.
    """
    out: dict[str, tuple[str, str, str]] = {}
    path = config.latest("letterboxd_ratings*.csv")
    if path:
        with open(path, newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                title, year = rec.get("Name", ""), rec.get("Year", "")
                uri = rec.get("Letterboxd URI", "")
                if title and uri:
                    out.setdefault(_key(title, year), (title, year, uri))
    watched, _ = _fetch.read_cache("letterboxd-cache.json", "watched")
    for entry in watched:
        title, year = entry.get("name", ""), str(entry.get("year") or "")
        uri = entry.get("letterboxd_uri", "")
        if title and uri:
            out.setdefault(_key(title, year), (title, year, uri))
    return list(out.values())


def run(limit: int | None = None) -> int:
    films = _films()
    if not films:
        print("fetch-letterboxd-avg: no ratings CSV and no diary cache — nothing to do")
        return 1

    cached, blob = _fetch.read_cache(CACHE, KEY)
    had = len(cached)
    seen = {e.get("_key") for e in cached}
    todo = [f for f in films if _key(f[0], f[1]) not in seen]
    if limit:
        todo = todo[:limit]

    print(f"  films {len(films):,}   cached {had:,}   to fetch {len(todo):,}")

    def flush() -> bool:
        try:
            _fetch.write_cache(CACHE, KEY, cached, had=had,
                               extra={k: v for k, v in blob.items() if k != KEY})
        except _fetch.CacheRefused as exc:
            print(f"fetch-letterboxd-avg: {exc}")
            return False
        return True

    missing = failed = 0
    for i, (title, year, uri) in enumerate(todo, 1):
        try:
            average = _average(uri)
        except Exception as exc:                       # noqa: BLE001 — one film, not the run
            failed += 1
            print(f"  {title[:44]:<46} failed ({type(exc).__name__})")
        else:
            if average is None:
                # The page loaded and carried no rating. Recorded as a miss so a
                # rerun does not fetch it forever; an unrated film is a fact.
                missing += 1
                cached.append({"_key": _key(title, year), "title": title,
                               "year": year, "avg_rating": None, "uri": uri})
            else:
                cached.append({"_key": _key(title, year), "title": title,
                               "year": year, "avg_rating": average, "uri": uri})
        if i % CHECKPOINT == 0:
            if not flush():
                return 1
            print(f"  [{i}/{len(todo)}] cached {len(cached):,}"
                  f"  (no rating {missing}, failed {failed})")
        time.sleep(DELAY)

    if not cached:
        print("fetch-letterboxd-avg: nothing fetched — refusing to write an empty cache")
        return 1
    if not flush():
        return 1

    rated = sum(1 for e in cached if e.get("avg_rating") is not None)
    print(f"  wrote {len(cached):,} films -> {CACHE}"
          f"  ({rated:,} with an average; this run: no rating {missing}, failed {failed})")
    return 0
