"""T0 anime — the MyAnimeList export, which is where the denominator lives.

The record already held television: 358 shows and 7,719 episodes off Trakt. What
it could not hold was *how far through* any of them the owner got, because Trakt
records what was watched and never how much there was to watch. Without a season
length "unfinished" is not a hard question, it is an unanswerable one — five
episodes is a third of a cour or the whole of a short, and nothing in the store
could tell those apart.

MAL carries the three things Trakt does not:

  a score        1-10, item by item, which is a rating television never had here
  a status       Watching / Completed / On-Hold / Dropped / Plan to Watch —
                 the owner's own word for what happened, not an inference
  a total        how many episodes the series has, which makes `watched` a
                 fraction rather than a count

One row per LIST ENTRY, which is what MAL files: a second season is its own
entry with its own total and its own status. That is deliberately not
reconciled against Trakt's one-show-many-seasons shape (see `titles.py`) —
within one MAL row `my_watched_episodes` and `series_episodes` count the same
thing, and any arithmetic that crosses the two sources would not.

`created` is the last time the list entry moved, falling back to the finish and
then the start date. MAL leaves an unset date as `0000-00-00`, and old exports
predate `my_last_updated` entirely, so a row with no date at all is normal and
must stay legible as "not dated" rather than as "watched at the epoch".

The id is minted from the MAL id alone, like `tv` mints from the Trakt id and
unlike `book`, whose id hashes its payload. That is the right choice for a row
that changes on every episode: hashing the payload would re-mint the row each
time a count ticks up, and the ledger would announce a show watched for a year
as recently added, every night (CONTRIBUTING).
"""
from __future__ import annotations

import datetime as dt
import glob
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import config
from ..provenance import Row, stable_id
from .titles import match_key, show_key

# MAL's own words -> one spelling this record can filter on. The numeric forms
# are what older exports wrote; both have been seen in the same account.
STATUS = {
    "watching": "watching", "1": "watching",
    "completed": "completed", "2": "completed",
    "on-hold": "on_hold", "on hold": "on_hold", "onhold": "on_hold", "3": "on_hold",
    "dropped": "dropped", "4": "dropped",
    "plan to watch": "plan_to_watch", "plantowatch": "plan_to_watch", "6": "plan_to_watch",
}


# What a MAL export is called by the time it reaches a disk. The site names it
# `animelist_<user>_-_<date>.xml.gz`; browsers unpack it inconsistently, and
# people rename it. All of them are the same XML, so the glob is wide and the
# file that is actually read is printed — a loader that silently found the wrong
# file is worse than one that found none.
PATTERNS = ("animelist*.xml", "animelist*.xml.gz",
            "myanimelist*.xml", "myanimelist*.xml.gz",
            "mal*.xml", "mal*.xml.gz")


def _find() -> Path | None:
    """The newest MAL export, gzipped or not.

    Newest by NAME, like every other dated export here: these files carry their
    date in the filename and a copy operation does not preserve mtime.
    """
    hits: list[str] = []
    for pat in PATTERNS:
        hits += glob.glob(str(config.EXPORTS / pat))
    return Path(sorted(set(hits))[-1]) if hits else None


def _text(node: ET.Element, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _int(node: ET.Element, tag: str) -> int:
    raw = _text(node, tag)
    try:
        return int(raw)
    except ValueError:
        return 0


def _date(raw: str) -> str:
    """A MAL date, or "" for the unset one.

    `0000-00-00` is MAL's null and it parses as a date in nothing — passing it
    through would put every undated entry at the start of every sort.
    """
    raw = (raw or "").strip()
    return "" if not raw or raw.startswith("0000") else raw


def _updated(raw: str) -> str:
    """`my_last_updated` is unix seconds. Rendered UTC, because it is an instant
    the site recorded rather than a day the owner named."""
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load() -> list[Row]:
    path = _find()
    if not path:
        print("  anime: no MyAnimeList export — skipping")
        return []
    print(f"  anime: reading {path.name}")
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError, gzip.BadGzipFile) as exc:
        # Loud, and empty. A half-parsed list would publish a denominator for
        # some titles and not others, which reads as "he never finished that
        # one" rather than as "the export did not load".
        print(f"  anime: could not read {path.name} ({exc}) — skipping")
        return []

    rows: list[Row] = []
    for a in root.findall("anime"):
        title = _text(a, "series_title")
        if not title:
            continue
        mal_id = _text(a, "series_animedb_id")
        status = STATUS.get(_text(a, "my_status").lower(), "")
        updated = _updated(_text(a, "my_last_updated"))
        finished = _date(_text(a, "my_finish_date"))
        started = _date(_text(a, "my_start_date"))
        url = f"https://myanimelist.net/anime/{mal_id}" if mal_id else ""
        rows.append(Row(
            tier="t0", zone="anime", source="myanimelist", author="external",
            # Last movement, then the day it was finished, then the day it was
            # started. None of the three is guaranteed; see the module note.
            created=(updated or finished or started or None),
            origin_ref=url or title,
            id=stable_id("mal_anime", mal_id or title),
            payload={
                "title": title,
                "mal_id": mal_id,
                # 0 is MAL's "unrated", not a rating of zero. Kept as it is
                # written so `ratings`' `> 0` filter means the same thing here
                # as it does for every other medium.
                "score": _int(a, "my_score"),
                "status": status,
                "episodes_watched": _int(a, "my_watched_episodes"),
                # 0 while a series is airing and its length is not yet known.
                # That is an absent denominator, and the surface must say so
                # rather than divide by it.
                "episodes_total": _int(a, "series_episodes"),
                "series_type": _text(a, "series_type"),
                "rewatches": _int(a, "my_times_watched"),
                "started": started,
                "finished": finished,
                "last_updated": updated,
                "url": url,
                # Best-effort, and only ever used to borrow a watch date off
                # Trakt. See titles.py for why it is not used for anything else.
                "match_key": match_key(title),
                # The coarser key, which DOES merge seasons. `watching` sums
                # `episodes_total` across every entry sharing it, because the
                # numerator it divides is Trakt's and Trakt counts a show.
                "show_key": show_key(title),
            },
        ))
    return rows
