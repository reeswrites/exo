"""A save's shape is read from what it is, not only where it came from.

The post queue words each shape differently (quote a tweet, upload a pin, link
an article, cross-post your own note), so a misread shape is a wrong post.
"""
from __future__ import annotations

import pytest

from exo import config
from exo.loaders.raindrop import _platform, _row, _shape


def shape(url: str, tags: str = "", kind: str = "link") -> str:
    return _shape(url, tags, kind, _platform(url))


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(config, "OWNER_HANDLES", ("substack.com/@ada",))


def test_own_note_beats_utterance(owner):
    url = "https://substack.com/@ada/note/c-1"
    assert shape(url, "IFTTT, Substack, Note, ada") == "own"


def test_no_handles_means_nothing_is_own(monkeypatch):
    monkeypatch.setattr(config, "OWNER_HANDLES", ())
    assert shape("https://substack.com/@ada/note/c-1") == "utterance"


@pytest.mark.parametrize("url,tags", [
    ("https://x.com/someone/status/1", "x, bookmark, tweet-only"),
    ("https://x.com/someone/status/1?s=12", ""),           # filed by hand, untagged
    ("https://substack.com/@kim/note/c-2", "substack, note-like, substack-only"),
    ("https://substack.com/@kim/note/c-3", ""),
    ("https://example.com/thread", "substack, comment-like, substack-only"),
])
def test_utterances(owner, url, tags):
    assert shape(url, tags) == "utterance"


@pytest.mark.parametrize("url,tags,kind", [
    ("https://i.pinimg.com/736x/a5/67/15/a5.jpg", "pinterest, image-only", "image"),
    ("https://www.pinterest.com/pin/123/", "", "link"),
    ("https://cdn.example.com/lamp.PNG?w=800", "", "link"),
    ("https://example.com/whatever", "", "image"),
])
def test_artifacts(url, tags, kind):
    assert shape(url, tags, kind) == "artifact"


@pytest.mark.parametrize("url,tags,kind", [
    ("https://kim.substack.com/p/an-essay", "substack, post-like", "article"),
    ("https://www.youtube.com/watch?v=abc", "IFTTT, YouTube", "video"),
    ("https://soundcloud.com/a/b", "IFTTT, SoundCloud", "audio"),
    ("https://borretti.me/article/futurism-is-always-extreme", "", "article"),
])
def test_pointers(url, tags, kind):
    assert shape(url, tags, kind) == "pointer"


def test_tag_match_is_exact_not_substring():
    # "comment" must not fire on a tag that merely contains it
    assert shape("https://example.com/a", "commentary") == "pointer"


def test_row_carries_shape_from_snapshot_string_tags():
    row = _row({"bookmark_id": 1, "link": "https://x.com/a/status/9", "tags": "x,bookmark,tweet-only",
                "type": "link", "created": "2026-08-18T20:05:26Z", "collection_id": "-1"}, {"-1": "Unsorted"})
    assert row.payload["shape"] == "utterance"


# ───────────── the note field: the owner's words, apart from the import's ─────────────
#
# Measured 2026-11-25: of 1,923 non-empty Raindrop notes, ~1,870 were written by
# the import scripts (a Substack note's text under `By:`, a tweet above
# `Tweet:`, a pin's description above `Pin:`). Served as the owner's note, they
# put a stranger's words and a pin's marketing copy in the owner's mouth.

from exo.loaders.raindrop import _split_note  # noqa: E402
from exo.provenance import stable_id  # noqa: E402


@pytest.mark.parametrize("raw,tags,want", [
    ("By: Kim\nCities make you lonely.", "substack, note-like, substack-only", ("", "Cities make you lonely.", "Kim")),
    ("By: Madison Huizinga", "substack, post-like", ("", "", "Madison Huizinga")),
    ("Tweet: https://x.com/a/status/1", "x, bookmark, tweet-only", ("", "", "")),
    ("Google, but for colors\nTweet: https://x.com/ph/status/2", "x, bookmark", ("", "Google, but for colors", "")),
    ("Loved by women in their 40s. Pin it now.\nPin: https://www.pinterest.com/pin/1/\nImage: https://i.pinimg.com/a.jpg",
     "pinterest", ("", "Loved by women in their 40s. Pin it now.", "")),
    ("nuclear semiotics and waste", "", ("nuclear semiotics and waste", "", "")),
    ("sex positivity, gen z", "IFTTT, YouTube", ("sex positivity, gen z", "", "")),
])
def test_only_the_owners_words_are_the_note(raw, tags, want):
    assert _split_note(raw, tags) == want


def _bm(**kw):
    base = {"bookmark_id": 7, "title": "t", "link": "https://example.com/a", "type": "link",
            "tags": "", "collection_id": "123", "created": "2026-01-01T00:00:00Z",
            "note": "", "excerpt": ""}
    return {**base, **kw}


def test_a_tweet_save_serves_the_tweet_as_post_text_not_as_the_owners_note():
    row = _row(_bm(link="https://x.com/k/status/1", tags=["x", "bookmark"],
                   note="cities make you lonely\nTweet: https://x.com/k/status/1"), {})
    assert row.payload["note"] == "" and row.payload["post_text"] == "cities make you lonely"


def test_the_owners_own_post_keeps_its_text_as_their_note(owner):
    row = _row(_bm(link="https://substack.com/@ada/note/c-1", tags="IFTTT, Substack, Note, ada",
                   note="By: Ada\nsomething I have started to prioritize"), {})
    assert row.payload["shape"] == "own" and row.payload["note"] == "something I have started to prioritize"


def test_unsorted_reads_as_a_name_not_a_number():
    assert _row(_bm(collection_id=-1), {}).payload["collection"] == "Unsorted"
    assert _row(_bm(collection_id=5), {"5": "yummy"}).payload["collection"] == "yummy"


def test_the_id_is_the_one_the_save_had_before_these_fields_existed():
    bm = _bm(tags=["x"], note="By: Kim\nhello", excerpt="e", collection_id="5")
    row = _row(bm, {"5": "yummy"})
    legacy = {"title": "t", "url": "https://example.com/a", "kind": "link", "collection": "yummy",
              "tags": "x", "platform": "example.com", "note": "By: Kim\nhello", "excerpt": "e"}
    assert row.id == stable_id("raindrop", "save", *[legacy[k] for k in sorted(legacy)])
