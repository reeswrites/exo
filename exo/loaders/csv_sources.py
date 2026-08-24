"""T0 consumption loaders — dated CSV exports UNIONED with the RSS/API caches.

Each returns list[Row] tagged tier=t0, author=external, grounds=True.

The CSVs are hand-made exports and go stale the moment you stop clicking Export;
the `*-cache.json` files next to them are the RSS/API pulls and are months
fresher (2026-08-18: film 2026-03-25 -> 2026-08-08, beer 2026-05-31 ->
2026-07-25, music 38,554 -> 39,703 scrobbles). Reading only the CSV left the
store four months behind data already sitting on the same disk.

So both are read and merged on a natural key: the cache supplies recency, the CSV
supplies whatever history predates the cache's window. Neither is authoritative
alone, and dropping either loses rows.

The caches are written by `exo fetch-lastfm`, `fetch-letterboxd`, `fetch-untappd`
and `fetch-goodreads`. Until ADR-0015 the first three were written by a fetcher
in another repository, on one machine, and this module only ever read them — so
the freshest copy of a consumption history was gated on that machine in a way
nothing in the pipeline named. The shape did not change when they moved: a cache
either fetcher wrote is one the other can resume from.

And they cannot be rebuilt. Letterboxd serves ~50 diary entries and Untappd ~25,
with no paging behind either, so everything older than that window exists in the
cache file and in the CSV and nowhere else. That is why the fetchers refuse to
write a cache that got smaller, and why these files belong wherever the record
is durable rather than wherever a run happens to reconstruct them.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime

from .. import config
from ..provenance import Row


def _cache(name: str) -> dict:
    """Read an RSS/API cache from Exo-owned raw/. Absent is not an error —
    the CSV path still works, just staler."""
    path = config.EXPORTS / name
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _merge(csv_rows: list[Row], cache_rows: list[Row], key) -> list[Row]:
    """CSV rows first, then any cache row whose key is not already present.

    Keyed rather than concatenated because the two overlap heavily — the cache
    usually contains most of the CSV. A key collision means the same event, so
    the CSV's version wins and only genuinely new rows are added.
    """
    seen = {key(r) for r in csv_rows}
    return csv_rows + [r for r in cache_rows if key(r) not in seen]


def _iso(s: str, fmt: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, fmt).isoformat()
    except ValueError:
        return None


def lastfm() -> list[Row]:
    """Scrobbles. Headerless: artist, album, track, '02 Jun 2026 03:20'."""
    path = config.latest("lastfm-2*.csv")
    if not path:
        return []
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 4:
                continue
            artist, album, track, ts = rec[0], rec[1], rec[2], rec[3]
            played = _iso(ts, "%d %b %Y %H:%M")
            rows.append(Row(
                tier="t0", zone="music", source="lastfm", author="external",
                created=played, origin_ref=path.name,
                payload={"artist": artist, "album": album, "track": track},
            ))

    cached = [
        Row(tier="t0", zone="music", source="lastfm", author="external",
            created=_iso(sc.get("scrobbled_at", ""), "%d %b %Y %H:%M"),
            origin_ref="lastfm-cache.json",
            payload={"artist": sc.get("artist", ""), "album": sc.get("album", ""),
                     "track": sc.get("song", "")})
        for sc in _cache("lastfm-cache.json").get("scrobbles", [])
    ]
    # Scrobbles do NOT merge by key. The CSV export and the API record the same
    # play with different clocks — 2026-06-02T03:20 in the export is
    # 2026-06-02T14:18 in the cache — so (artist, track, time) matches nothing
    # and a keyed merge silently doubles the corpus (38,554 -> 78,257 when first
    # tried). A scrobble stream is append-only, so splice on time instead: keep
    # the CSV entire, take from the cache only what is newer than the CSV's last
    # play. Overlap becomes impossible rather than merely unlikely.
    cutoff = max((r.created for r in rows if r.created), default="")
    return rows + [r for r in cached if r.created and r.created > cutoff]


def letterboxd() -> list[Row]:
    """Film ratings. Header: Date,Name,Year,Letterboxd URI,Rating."""
    path = config.latest("letterboxd_ratings*.csv")
    if not path:
        return []
    # The community average, from `exo fetch-letterboxd-avg`. Absent is not an
    # error — the column comes through empty and only the null-model check goes
    # missing with it. Keyed title+year, the same identity _film_key uses below.
    _avgs = {e.get("_key"): e.get("avg_rating")
             for e in _cache("letterboxd-avg-cache.json").get("films", [])}

    def _avg(title: str, year) -> str:
        hit = _avgs.get(f"{(title or '').strip().lower()}|{str(year or '').strip()}")
        return "" if hit is None else str(hit)

    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            title, year = rec.get("Name", ""), rec.get("Year", "")
            rows.append(Row(
                tier="t0", zone="film", source="letterboxd", author="external",
                created=(rec.get("Date") or None), origin_ref=rec.get("Letterboxd URI", ""),
                payload={
                    "title": title, "year": year,
                    "rating": rec.get("Rating", ""),
                    "avg_rating": _avg(title, year),
                },
            ))

    cached = [
        Row(tier="t0", zone="film", source="letterboxd", author="external",
            created=(w.get("date") or None), origin_ref=w.get("letterboxd_uri", ""),
            payload={"title": w.get("name", ""), "year": str(w.get("year") or ""),
                     "rating": str(w.get("rating") or ""),
                     "avg_rating": _avg(w.get("name", ""), w.get("year"))})
        for w in _cache("letterboxd-cache.json").get("watched", [])
    ]
    # Keyed on title+year, NOT on the uri. Letterboxd hands out a different
    # permalink per context — https://boxd.it/2a38 for the film, a separate
    # boxd.it id for the diary entry, and letterboxd.com/<user>/film/... for the
    # member page — so keying on the uri counted one watch of The Conversation three
    # times and inflated the library by 116 phantom films. The dates disagree
    # too (2026-03-18 vs -19, a timezone), so they cannot break the tie either.
    #
    # This collapses genuine rewatches to one row. That is the right trade here
    # — the owner does not rewatch, and the alternative was 117 films that do not
    # exist. If rewatch tracking is ever wanted, it needs the diary entry id as
    # its own field rather than as identity.
    def _film_key(r):
        return ((r.payload.get("title") or "").strip().lower(),
                str(r.payload.get("year") or "").strip())
    return _merge(rows, cached, _film_key)


def goodreads() -> list[Row]:
    """Books. Standard Goodreads export (24 cols)."""
    path = config.latest("goodreads*.csv")
    if not path:
        return []
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            read = _iso(rec.get("Date Read", ""), "%Y/%m/%d")
            added = _iso(rec.get("Date Added", ""), "%Y/%m/%d")
            rows.append(Row(
                tier="t0", zone="book", source="goodreads", author="external",
                created=(read or added), origin_ref=str(rec.get("Book Id", "")),
                payload={
                    "title": rec.get("Title", ""), "book_author": rec.get("Author", ""),
                    "my_rating": rec.get("My Rating", ""),
                    "avg_rating": rec.get("Average Rating", ""),
                    "shelf": rec.get("Exclusive Shelf", ""),
                    # Kept apart on purpose. `created` collapses to date_added
                    # when a finish date is missing, and only 108 of 448 read
                    # books have one — so a row's date can mean "finished then"
                    # or "shelved then", and nothing downstream could tell.
                    "date_read": read,
                    "date_added": added,
                },
            ))

    # goodreads-cache.json is written by `wh fetch-goodreads` from the RSS
    # shelves — the export CSV stopped at 2026-03-25 while the feed runs to
    # today. Book ids are stable, so this merges by key like film and beer.
    cached = [
        Row(tier="t0", zone="book", source="goodreads", author="external",
            created=(b.get("date_read") or b.get("date_added") or None),
            origin_ref=str(b.get("book_id", "")),
            payload={"title": b.get("title", ""), "book_author": b.get("book_author", ""),
                     "my_rating": str(b.get("my_rating") or ""),
                     "avg_rating": str(b.get("avg_rating") or ""),
                     "shelf": b.get("shelf", ""),
                     "date_read": b.get("date_read"),
                     "date_added": b.get("date_added")})
        for b in _cache("goodreads-cache.json").get("books", [])
    ]
    return _merge(rows, cached, lambda r: r.origin_ref)


def untappd() -> list[Row]:
    """Beer check-ins. Header has a BOM; utf-8-sig strips it. Native untappd
    column names kept verbatim (venue_lat, brewery_city, …) so the travel
    vertical can read t0_beer without a lossy remap."""
    path = config.latest("untappd*.csv")
    if not path:
        return []
    keep = ["beer_name", "brewery_name", "beer_type", "beer_abv", "rating_score",
            "venue_name", "venue_city", "venue_state", "venue_country",
            "venue_lat", "venue_lng", "brewery_city", "brewery_state",
            "brewery_country", "comment"]
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            rows.append(Row(
                tier="t0", zone="beer", source="untappd", author="external",
                created=(rec.get("created_at") or None), origin_ref=rec.get("checkin_url", ""),
                payload={c: rec.get(c, "") for c in keep},
            ))

    cached = [
        Row(tier="t0", zone="beer", source="untappd", author="external",
            created=(ch.get("created_at") or None), origin_ref=ch.get("checkin_url", ""),
            payload={c: str(ch.get(c, "") or "") for c in keep})
        for ch in _cache("untappd-cache.json").get("checkins", [])
    ]
    # checkin_url is unique per check-in.
    return _merge(rows, cached, lambda r: r.origin_ref)


# name -> loader; the CLI iterates this
REGISTRY = {
    "music": lastfm,
    "film": letterboxd,
    "book": goodreads,
    "beer": untappd,
}
