"""T0 television — Trakt's watched shows.

An entire medium was missing. He has written verdicts about Death Note, Severance
and Lie to Me, so an assistant could read his opinions on television while having
no record that he watches any: 358 shows and 7,719 episodes sat in
`trakt-cache.json` with no loader.

One row per SHOW, not per episode. 7,719 episode rows would dominate every
count in the store — more rows than notes and atoms combined — while answering
nothing an assistant asks. "What does he watch" is a question about shows;
episode counts belong on the show as a measure of how far he got.

`created` is last_watched_at, so recency means the same thing it does for films
and scrobbles rather than "when the row was written".
"""
from __future__ import annotations

import json

from .. import config
from ..provenance import Row, stable_id


def load() -> list[Row]:
    path = config.EXPORTS / "trakt-cache.json"
    if not path.exists():
        print("  tv: no trakt cache — skipping")
        return []
    try:
        shows = json.loads(path.read_text(encoding="utf-8")).get("shows", [])
    except (json.JSONDecodeError, OSError):
        return []

    rows: list[Row] = []
    for s in shows:
        show = s.get("show") or {}
        title = show.get("title")
        if not title:
            continue
        ids = show.get("ids") or {}
        seasons = s.get("seasons") or []
        episodes = sum(len(se.get("episodes") or []) for se in seasons)
        slug = ids.get("slug")
        rows.append(Row(
            tier="t0", zone="tv", source="trakt", author="external",
            created=s.get("last_watched_at"),
            # A link, like films have — an assistant that can name a show should
            # be able to point at it.
            origin_ref=f"https://trakt.tv/shows/{slug}" if slug else str(ids.get("trakt", "")),
            id=stable_id("trakt_show", ids.get("trakt") or title),
            payload={
                "title": title,
                "year": str(show.get("year") or ""),
                "plays": s.get("plays") or 0,
                "episodes_watched": episodes,
                "seasons_touched": len(seasons),
                "imdb": ids.get("imdb") or "",
                "url": f"https://trakt.tv/shows/{slug}" if slug else "",
            },
        ))
    return rows
