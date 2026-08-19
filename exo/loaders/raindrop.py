"""T0 raindrop loader — saves as a consumption signal, not just recap fodder.

Reads a snapshot at zones/_snapshots/raindrop.json (pulled via the raindrop
MCP; see `_snapshots/raindrop.json._note`). If RAINDROP_TOKEN is set it will
instead page the live API for a full sync. The snapshot is a bounded sample —
we log the cap so it never reads as "the whole library".

zone="save"; payload carries title, url, kind (video/audio/article/link),
collection (human-readable), tags. created = save time.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

from .. import config
from ..provenance import Row

_SNAPSHOT = config.SNAPSHOTS / "raindrop.json"


def _to_iso(s: str) -> str | None:
    """Save time -> ISO8601, accepting both formats raindrop hands us.

    The live API returns ISO ("2024-01-15T10:00:00.000Z"); the MCP snapshot stores
    RFC ("Sat, 08 Aug 2026 18:33:39 GMT"). Feeding an ISO string to the RFC parser
    yields None, which silently drops every date on a full API sync — so try ISO first.
    """
    if not s:
        return None
    try:  # ISO (API)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    try:  # RFC (snapshot)
        return datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


_PLATFORMS = (
    ("youtube.com", "youtube"), ("youtu.be", "youtube"),
    ("x.com", "x"), ("twitter.com", "x"),
    ("substack.com", "substack"),
    ("pinimg.com", "pinterest"), ("pinterest.", "pinterest"),
    ("tiktok.com", "tiktok"),
    ("soundcloud.com", "soundcloud"), ("bandcamp.com", "bandcamp"),
    ("spotify.com", "spotify"),
    ("reddit.com", "reddit"), ("github.com", "github"),
    ("instagram.com", "instagram"), ("vimeo.com", "vimeo"),
    ("etsy.com", "etsy"), ("amazon.com", "amazon"),
    ("letterboxd.com", "letterboxd"), ("goodreads.com", "goodreads"),
)


def _platform(url: str) -> str:
    """Where a save came from, derived once at load rather than regexed per query.

    `kind` says what a thing IS (video, article); platform says where it lives,
    and they answer different questions — "what videos were saved" is not "what
    was saved from YouTube", since a YouTube link the API never typed comes
    through as a plain link.
    """
    u = (url or "").lower()
    for needle, name in _PLATFORMS:
        if needle in u:
            return name
    host = u.split("//", 1)[-1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _tags(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return ", ".join(t.strip() for t in raw.split(",") if t.strip())
    return ", ".join(str(t).strip() for t in raw if str(t).strip())


def _row(bm: dict, collections: dict[str, str]) -> Row:
    cid = str(bm.get("collection_id", ""))
    return Row(
        tier="t0", zone="save", source="raindrop", author="external",
        created=_to_iso(bm.get("created", "")),
        origin_ref=str(bm.get("bookmark_id", "")),
        payload={
            "title": bm.get("title", ""),
            "url": bm.get("link", ""),
            "kind": bm.get("type", "link"),
            "collection": collections.get(cid, cid),
            # Accept both shapes. The live API hands back a list; the snapshot
            # now stores a comma-joined string, and `", ".join(a_string)`
            # silently iterates characters — 1,948 saves came through as
            # "I, F, T, T, T, ,, Y, o, u..." rather than "IFTTT, YouTube",
            # which reads as data rather than as a bug.
            "tags": _tags(bm.get("tags")),
            "platform": _platform(bm.get("link", "")),
            # `note` is the OWNER's commentary on why a link was worth keeping —
            # the same category as verdicts and reviews, and 1,906 saves have one.
            # `excerpt` is the page's own description, which is the world's
            # words; kept apart so an assistant never quotes marketing copy back
            # as if it were an authored thought.
            "note": (bm.get("note") or "").strip(),
            "excerpt": (bm.get("excerpt") or "").strip(),
        },
    )


def _from_api(token: str) -> list[Row]:
    """Full sync via the raindrop.io API (paged). Only if a token is present."""
    def get(url: str) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (trusted host)
            return json.load(r)

    cols = {str(c["_id"]): c["title"] for c in get("https://api.raindrop.io/rest/v1/collections").get("items", [])}
    rows: list[Row] = []
    page = 0
    while True:
        data = get(f"https://api.raindrop.io/rest/v1/raindrops/0?perpage=50&page={page}")
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            rows.append(_row(
                {"bookmark_id": it.get("_id"), "title": it.get("title"), "type": it.get("type"),
                 "collection_id": (it.get("collection") or {}).get("$id"),
                 "created": it.get("created"), "link": it.get("link"), "tags": it.get("tags"),
                 # The MCP's find_bookmarks does not return these; the REST API
                 # does. Dropping them here is why a token buys the commentary
                 # and not merely fresher links.
                 "note": it.get("note"), "excerpt": it.get("excerpt")},
                cols,
            ))
        page += 1
    print(f"  raindrop: full API sync, {len(rows)} rows")
    return rows


def load() -> list[Row]:
    token = os.environ.get("RAINDROP_TOKEN")
    if token:
        return _from_api(token)
    if not _SNAPSHOT.exists():
        print("  raindrop: no snapshot and no RAINDROP_TOKEN — skipping")
        return []
    snap = json.loads(_SNAPSHOT.read_text())
    cols = snap.get("collections", {})
    rows = [_row(bm, cols) for bm in snap.get("bookmarks", [])]
    total = snap.get("total")
    if total and total > len(rows):
        print(f"  raindrop: snapshot sample — {len(rows)} of {total} saves "
              f"(set RAINDROP_TOKEN for full sync)")
    return rows
