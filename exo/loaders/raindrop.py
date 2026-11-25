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
from ..provenance import Row, stable_id

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


# Tags the bulk-import userscripts stamp on a save whose content IS the post —
# no external page behind it. Matched exactly, against the joined tag list.
_UTTERANCE_TAGS = {"tweet-only", "note-like", "comment-like", "comment"}
_ARTIFACT_TAGS = {"image-only"}
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _shape(url: str, tags: str, kind: str, platform: str) -> str:
    """What a save IS, which decides how it can be passed on.

    - `own` — the owner's own published work (matched on `owner.handles`)
    - `utterance` — someone's words, where the post is the content (a tweet, a note)
    - `artifact` — an image that is the content (a pin)
    - `pointer` — a link to something that lives elsewhere (article, video, track)

    Platform alone cannot answer this: a Substack link is an utterance when it is
    a note and a pointer when it is a post, and an x.com link is a tweet either
    way. Own is checked first — the owner's note is also a note.
    """
    u = (url or "").lower()
    tagset = {t.strip().lower() for t in tags.split(",") if t.strip()}
    if any(h in u for h in config.OWNER_HANDLES):
        return "own"
    if tagset & _UTTERANCE_TAGS or "/status/" in u or (platform == "substack" and "/note/" in u):
        return "utterance"
    if tagset & _ARTIFACT_TAGS or platform == "pinterest" or kind == "image" \
            or u.split("?", 1)[0].endswith(_IMAGE_EXT):
        return "artifact"
    return "pointer"


# Lines the import userscripts write INTO Raindrop's note field. None of them is
# the owner talking: a pointer back to the post, or a pin's image. Raindrop has
# one note field, and the scripts used it to carry the post along.
_STAMP_PREFIXES = ("tweet: http", "pin: http", "image: http", "read: http")
_IMPORT_TAGS = {"substack", "pinterest", "bookmark"}


def _split_note(raw: str, tags: str) -> tuple[str, str, str]:
    """Raindrop's note field -> (owner's note, the post's own text, its author).

    Measured on the 2,480-save library (2026-11-25): of 1,923 non-empty notes,
    925 were Substack imports (`By: <author>` then the note's text), 486 were X
    (the tweet's text, then `Tweet: <url>`) and 496 Pinterest (the pin's
    description, then `Pin:` / `Image:`). About 50 were the owner's own words.
    Served as the owner's commentary, the rest put a stranger's tweet and a
    pin's marketing copy in their mouth.

    So: stamp lines are dropped; on a save an import script filed (its tags, or
    a stamp line present) what remains is the POST's text; anything else is the
    owner's. Saves of the owner's own posts are theirs either way — `_shape`
    marks them `own` and the text stays in `note`.
    """
    lines = [l for l in (raw or "").replace("\r", "").split("\n")]
    stamped = any(l.strip().lower().startswith(_STAMP_PREFIXES) for l in lines)
    by = ""
    if lines and lines[0].startswith("By: "):
        by, lines, stamped = lines[0][4:].strip(), lines[1:], True
    body = "\n".join(l for l in lines
                     if not l.strip().lower().startswith(_STAMP_PREFIXES)).strip()
    tagset = {t.strip().lower() for t in tags.split(",") if t.strip()}
    imported = stamped or bool(tagset & _IMPORT_TAGS)
    return ("", body, by) if imported else (body, "", by)


def _tags(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return ", ".join(t.strip() for t in raw.split(",") if t.strip())
    return ", ".join(str(t).strip() for t in raw if str(t).strip())


# Raindrop's id for the Unsorted bucket. The API lists it with no title, so it
# came through as a bare "-1" — 256 saves filed under a number.
_UNSORTED = {"-1": "Unsorted", "": "Unsorted"}


def _row(bm: dict, collections: dict[str, str]) -> Row:
    cid = str(bm.get("collection_id", ""))
    url, tags = bm.get("link", ""), _tags(bm.get("tags"))
    kind, platform = bm.get("type", "link"), _platform(bm.get("link", ""))
    raw_note = (bm.get("note") or "").strip()
    note, post_text, by = _split_note(raw_note, tags)
    shape = _shape(url, tags, kind, platform)
    if shape == "own" and not note:
        # The owner's own post: its text is theirs, wherever the import put it.
        note, post_text = post_text, ""
    # The id is minted from the payload as it was BEFORE shape, post_text, by and
    # the note split existed: a save's identity did not change when its fields
    # were read more carefully, and re-minting would make all 2,480 look newly
    # saved (first_seen is keyed on the id).
    legacy = {
        "title": bm.get("title", ""), "url": url, "kind": kind,
        "collection": collections.get(cid, cid), "tags": tags, "platform": platform,
        "note": raw_note, "excerpt": (bm.get("excerpt") or "").strip(),
    }
    return Row(
        tier="t0", zone="save", source="raindrop", author="external",
        created=_to_iso(bm.get("created", "")),
        origin_ref=str(bm.get("bookmark_id", "")),
        id=stable_id("raindrop", "save", *[legacy[k] for k in sorted(legacy)]),
        payload={
            "title": bm.get("title", ""),
            "url": url,
            "kind": kind,
            "collection": collections.get(cid) or _UNSORTED.get(cid, cid),
            # Accept both shapes. The live API hands back a list; the snapshot
            # now stores a comma-joined string, and `", ".join(a_string)`
            # silently iterates characters — 1,948 saves came through as
            # "I, F, T, T, T, ,, Y, o, u..." rather than "IFTTT, YouTube",
            # which reads as data rather than as a bug.
            "tags": tags,
            "platform": platform,
            # How the save can be passed on (see _shape). Derived here, not
            # stored in Raindrop, so a rule change reclassifies the whole
            # library on the next ingest.
            "shape": shape,
            # `note` is the OWNER's commentary on why a link was worth keeping —
            # the same category as verdicts and reviews. About 50 saves have one;
            # the import scripts' text moved to `post_text` (see _split_note).
            # `excerpt` is the page's own description, which is the world's
            # words; kept apart so an assistant never quotes marketing copy back
            # as if it were an authored thought.
            "note": note,
            # The saved post's own words (a tweet, a Substack note, a pin's
            # description) and who wrote them — never the owner's (_split_note).
            "post_text": post_text,
            "by": by,
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


def _from_cache() -> list[Row] | None:
    """The library as `exo fetch-raindrop` last wrote it, or None if it never has.

    Preferred to both the live API and the snapshot: it is the whole library
    with notes and excerpts (unlike the snapshot), and reading it keeps a rebuild
    offline (unlike paging the API from inside ingest)."""
    path = config.EXPORTS / "raindrop-cache.json"
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        print("  raindrop: raindrop-cache.json is unreadable — falling back")
        return None
    cols = blob.get("collections", {})
    rows = [_row(bm, cols) for bm in blob.get("bookmarks", [])]
    print(f"  raindrop: {len(rows):,} saves from the API cache")
    return rows or None


def load() -> list[Row]:
    cached = _from_cache()
    if cached is not None:
        return cached
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
