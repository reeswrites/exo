"""The brief's byte budget protects the capability index.

ADR-0013 §2: publishing is not offering. The brief is the only thing a client
reads unprompted, so a tool it does not name is effectively unshipped — and for
a while the brief stopped naming `releases`, the music press and the film pool
without anyone deciding it should, because the index sat below the prose and a
9,000-byte slice off the bottom took it first. worker/test/run.mjs checks the
live brief names every offered tool; these check the mechanism that makes that
promise keepable on any instance:

  - the index alone, with every optional entry switched on and every count at
    an absurd width, fits inside MAX_BYTES with room to spare — so no amount of
    prose growth above it can push an entry off;
  - when the brief IS over budget, whole prose sections leave (and say where to
    ask for them) while the index, the hard constraints and the guarded tail
    arrive intact;
  - the brief the instance actually published was not clipped.

The first two need no record: build() tolerates absent zones (`_q`), so an
empty directory plus a `served_counts` dict is a complete brief with a real
index and no prose. That is deliberate — the budget is a property of the
engine, and a test of it should not wait for someone to point EXO_HOME at data.
"""
from __future__ import annotations

import pathlib
import re

import duckdb
import pytest

from exo import config
from exo.scripts_impl import brief


# Every zone whose count switches an index entry on, at a width no real
# instance will reach — eight digits with separators is the worst case a count
# can cost the budget. If an entry is added behind a new count, add it here, or
# this test measures an index smaller than the one that ships.
_EVERYTHING = {z: 12_345_678 for z in (
    "t1_notes", "t2_atom", "t0_music", "t0_music_tag", "t0_album_pool", "t0_film", "t0_tv",
    "t0_beer", "t1_verdicts", "t1_open_thread", "t1_post", "t1_project",
    "t1_project_commit", "t1_project_doc", "t1_visits", "t1_item",
    "t1_item_event", "t1_draft", "t1_recipe", "t0_chat_topic", "t0_raindrop",
    "t0_event", "t0_release", "t0_film_offer", "t0_criticism", "t1_taste",
    "t0_taste_derived",
)}

# The tools the index names on an instance with every entry on. The worker test
# holds the published brief to the instance's actual tool list; this holds the
# builder to the whole engine's, independently of any instance. `streaming` in
# particular is withheld on an instance with no film pool, so only this test
# sees whether its entry still renders.
_ENGINE_TOOLS = (
    "whats_relevant", "notes_on", "albums", "unheard", "consumption", "taste", "ratings",
    "facets", "watching", "reviews", "verdicts", "open_threads", "posts",
    "projects", "project_activity", "project_docs", "project_open", "places",
    "agenda", "history", "drafts", "recipes", "medium", "backlog",
    "around_the_time", "collection", "recent_topics", "thread", "saves", "events", "releases",
    "streaming", "criticism", "taste_profile", "taste_summary",
)


def _write(tmp_path, name: str, select: str) -> None:
    con = duckdb.connect(":memory:")
    con.execute(f"COPY ({select}) TO '{tmp_path / name}.parquet' (FORMAT parquet)")
    con.close()


def _record(path):
    """A projection with no prose in it, only what the index reads directly.

    Two index entries are gated on a zone's ROWS rather than on a served count
    — the ownership line lists kinds, the anime line divides watched by queued
    — so an empty directory leaves them out and the index measured here would
    be smaller than the one that ships. Written at worst-case width.
    """
    path.mkdir(exist_ok=True)
    _write(path, "t1_collection", """
        SELECT k AS kind FROM (VALUES ('vinyl'), ('dvd'), ('board_game'), ('fragrance'),
                                      ('cassette'), ('book'), ('poster'), ('zine')) t(k),
                              range(12345)""")
    _write(path, "t0_anime", """
        SELECT CASE WHEN i % 5 = 0 THEN 'plan_to_watch' ELSE 'completed' END AS status,
               i % 10 AS score FROM range(123456) r(i)""")
    return path


def _index(text: str) -> str:
    start = text.index("## What you can ask this surface for")
    end = text.index("\n\n", start)
    return text[start:end]


def test_every_engine_tool_is_named_in_the_index(tmp_path):
    text = brief.build(dict(_EVERYTHING), src=_record(tmp_path))
    index = _index(text)
    missing = [t for t in _ENGINE_TOOLS if f"`{t}`" not in index and f"`{t}(" not in index]
    assert not missing, f"the index does not name: {missing}"


def test_worker_and_engine_agree_on_the_tool_list():
    # _ENGINE_TOOLS is a second spelling of TOOLS in worker/src/tools.js, and
    # two spellings drift. Parsed rather than imported — pytest has no node —
    # from the one shape every entry takes: two-space indent, name, `: {`.
    src = pathlib.Path(brief.__file__).parents[2] / "worker" / "src" / "tools.js"
    names = set(re.findall(r"^  ([a-z_]+): \{", src.read_text(), re.M))
    assert names == set(_ENGINE_TOOLS), (
        f"brief tests out of step with TOOLS — only in tools.js: {sorted(names - set(_ENGINE_TOOLS))}, "
        f"only here: {sorted(set(_ENGINE_TOOLS) - names)}")


def test_the_protected_parts_fit_with_room_for_prose(tmp_path):
    # With no prose at all, what is left is exactly what can never yield: the
    # title, the index and the guarded tail. It has to leave real room — a
    # quarter of the budget — or the prose sections will be yielding on every
    # instance, which is correct but means the brief has stopped doing half its
    # job. Past this line, trim the index entries before raising MAX_BYTES.
    text = brief.build(dict(_EVERYTHING), src=_record(tmp_path))
    assert brief.CLIPPED_MARKER not in text
    assert len(text.encode()) <= brief.MAX_BYTES * 3 // 4, (
        f"protected parts alone are {len(text.encode()):,} of {brief.MAX_BYTES:,} bytes")


def test_over_budget_yields_prose_whole_and_keeps_the_index(tmp_path, monkeypatch):
    # Prose that cannot fit: three verdicts at the full quoted width and five
    # open questions. The budget is squeezed to just above the protected parts,
    # so something must go — and what goes must be whole sections, never the
    # index, and never a verbatim quote cut mid-sentence.
    protected = len(brief.build(dict(_EVERYTHING), src=_record(tmp_path)).encode())
    note = "This is a long verbatim verdict about the thing, written in full. " * 6
    _write(tmp_path, "t1_verdicts", f"""
        SELECT * FROM (VALUES ('Alpha','film',5,'{note}'), ('Beta','book',4,'{note}'),
                              ('Gamma','music',3,'{note}')) t(subject, kind, rating, note)""")
    _write(tmp_path, "t1_open_thread", """
        SELECT 'What would it take to make the brief never lose an entry again?' AS question,
               'open' AS state, '2026-09-0' || i AS created FROM range(1, 6) r(i)""")

    roomy = brief.build(dict(_EVERYTHING), src=tmp_path)
    assert "## How" in roomy and "## Currently open questions" in roomy

    monkeypatch.setattr(brief, "MAX_BYTES", protected + 400)
    tight = brief.build(dict(_EVERYTHING), src=tmp_path)

    assert len(tight.encode()) <= brief.MAX_BYTES
    assert brief.CLIPPED_MARKER not in tight
    assert _index(tight) == _index(roomy), "the index changed under budget pressure"
    assert "## Freshness" in tight
    # Verdicts leave before questions (_YIELD_ORDER), and say where they went.
    assert "## How" not in tight
    assert "verbatim verdicts — ask `verdicts`" in tight
    # Whatever survived is whole: no quote in the brief is cut short.
    for line in tight.splitlines():
        if line.startswith("- **") and '— "' in line:
            assert line.endswith('"'), f"a verbatim line was sliced: {line[:80]}"


def test_last_resort_clip_still_says_so(tmp_path, monkeypatch):
    # If the protected parts alone overrun, the old byte clip is all that is
    # left, and a brief that lost entries must not read as complete.
    monkeypatch.setattr(brief, "MAX_BYTES", 1500)
    text = brief.build(dict(_EVERYTHING), src=_record(tmp_path))
    assert brief.CLIPPED_MARKER in text
    assert "## Freshness" in text


def test_the_published_brief_was_not_clipped():
    # The instance's own brief, as last published — the fixture or the live
    # record, whichever EXO_HOME names. Skipped, loudly, when nothing has been
    # published there, for the same reason the `record` tests are.
    path = config.SERVE / "brief.md"
    if not path.exists():
        pytest.skip(f"no published brief at {path} — run `exo publish` against "
                    "this EXO_HOME to check it")
    text = path.read_text(encoding="utf-8")
    assert brief.CLIPPED_MARKER not in text, "the capability index was byte-clipped"
    assert "(left out to fit:" not in text, (
        "a prose section yielded — within one growth spurt of clipping; trim the index")
    assert len(text.encode()) <= brief.MAX_BYTES
