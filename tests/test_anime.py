"""The MyAnimeList loader, and the join key that lets it complete t0_tv.

Two things are being protected here and they fail differently.

The LOADER has to survive the shapes a real export takes: `0000-00-00` for a
date nobody set, `0` for a score nobody gave and for a series whose length is
not settled, statuses spelled as words in one era and as numbers in another,
CDATA around every title, and a `my_last_updated` that older exports simply do
not carry. Every one of those, read wrong, produces a plausible number rather
than an error — an entry watched at the epoch, a series rated zero, a show
"finished" because 9 >= 0.

The JOIN KEY has to be stable and it has to be honest. It is stable because it
is not part of any id (both loaders pass an explicit `id=`), and it is honest
because it is only ever trusted with a date — see `exo/loaders/titles.py`.
"""
from __future__ import annotations

import gzip

import pytest

from exo import config
from exo.loaders import anime, tv
from exo.loaders.titles import match_key

ENTRY = """<?xml version="1.0" encoding="UTF-8" ?>
<myanimelist>
  <myinfo><user_name>somebody</user_name></myinfo>
  {body}
</myanimelist>
"""

ONE = """  <anime>
    <series_animedb_id>{id}</series_animedb_id>
    <series_title><![CDATA[{title}]]></series_title>
    <series_type>TV</series_type>
    <series_episodes>{total}</series_episodes>
    <my_watched_episodes>{watched}</my_watched_episodes>
    <my_start_date>{start}</my_start_date>
    <my_finish_date>{finish}</my_finish_date>
    <my_score>{score}</my_score>
    <my_status>{status}</my_status>
    <my_times_watched>0</my_times_watched>
    {extra}
  </anime>
"""


def entry(id="1", title="A Show", total=12, watched=3, start="0000-00-00",
          finish="0000-00-00", score=0, status="Watching", extra=""):
    return ONE.format(id=id, title=title, total=total, watched=watched, start=start,
                      finish=finish, score=score, status=status, extra=extra)


@pytest.fixture
def exports(monkeypatch, tmp_path):
    """An exports directory holding whatever a test writes into it."""
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(config, "EXPORTS", d)
    return d


def load(exports, body, name="animelist-2026-06-05.xml"):
    (exports / name).write_text(ENTRY.format(body=body), encoding="utf-8")
    return anime.load()


def one(exports, **kw):
    rows = load(exports, entry(**kw))
    assert len(rows) == 1
    return rows[0]


def test_no_export_is_not_an_error(exports):
    """A record with no MAL list is a normal record, not a broken one."""
    assert anime.load() == []


def test_a_gzipped_export_reads_the_same(exports):
    """MAL hands out .xml.gz and browsers unpack it inconsistently."""
    (exports / "animelist-2026-06-05.xml.gz").write_bytes(
        gzip.compress(ENTRY.format(body=entry(title="Zipped")).encode()))
    rows = anime.load()
    assert [r.payload["title"] for r in rows] == ["Zipped"]


@pytest.mark.parametrize("name", [
    "animelist_1000001_-_2026-06-05.xml",
    "myanimelist-2026-06-05.xml",
    "mal-2026-06-05.xml",
])
def test_it_finds_the_export_under_the_names_it_arrives_with(exports, name):
    """The site's own name, and the two it gets renamed to. All the same XML,
    and a loader that quietly finds none of them looks exactly like an empty
    list."""
    rows = load(exports, entry(title="Found"), name=name)
    assert [r.payload["title"] for r in rows] == ["Found"]


def test_the_newest_export_wins(exports):
    load(exports, entry(id="1", title="Older"), name="animelist-2026-01-01.xml")
    rows = load(exports, entry(id="2", title="Newer"), name="animelist-2026-06-05.xml")
    assert [r.payload["title"] for r in rows] == ["Newer"]


@pytest.mark.parametrize("written,parsed", [
    ("Watching", "watching"),
    ("Completed", "completed"),
    ("On-Hold", "on_hold"),
    ("Dropped", "dropped"),
    ("Plan to Watch", "plan_to_watch"),
    # What older exports wrote instead of the words.
    ("1", "watching"), ("2", "completed"), ("3", "on_hold"), ("4", "dropped"), ("6", "plan_to_watch"),
])
def test_every_status_lands_in_one_vocabulary(exports, written, parsed):
    assert one(exports, status=written).payload["status"] == parsed


def test_an_unknown_status_is_empty_rather_than_guessed(exports):
    """The surface derives a status when MAL has none. Inventing one here would
    put a word in the owner's mouth that they never said."""
    assert one(exports, status="Rewatching").payload["status"] == ""


def test_the_null_date_does_not_become_a_date(exports):
    """`0000-00-00` is MAL's unset. Passed through, every undated entry leads
    every sort that this record does by date."""
    r = one(exports, start="0000-00-00", finish="0000-00-00")
    assert r.payload["started"] == "" and r.payload["finished"] == ""
    assert r.created is None


def test_created_prefers_the_last_move_then_the_finish_then_the_start(exports):
    updated = "<my_last_updated>1726358400</my_last_updated>"
    assert one(exports, start="2026-01-02", finish="2026-03-04",
               extra=updated).created == "2024-09-15T00:00:00Z"
    assert one(exports, start="2026-01-02", finish="2026-03-04").created == "2026-03-04"
    assert one(exports, start="2026-01-02").created == "2026-01-02"


def test_a_zero_is_absent_rather_than_low(exports):
    """MAL writes 0 for a score nobody gave and for a length nobody knows yet.
    Both are read as absent downstream; what must not happen here is either one
    being silently turned into something else."""
    r = one(exports, score=0, total=0, watched=9)
    assert r.payload["score"] == 0
    assert r.payload["episodes_total"] == 0
    assert r.payload["episodes_watched"] == 9


def test_the_id_survives_another_episode(exports):
    """The id is minted from the MAL id, never from the payload.

    A row that re-mints when a count ticks up makes the ledger announce a show
    watched for a year as recently added, every night it is watched
    (CONTRIBUTING). t0_tv already avoids this; the anime list changes far more
    often than it does.
    """
    before = one(exports, id="9001", watched=3, score=0)
    after = one(exports, id="9001", watched=4, score=8, status="Completed")
    assert before.id == after.id
    assert after.payload["episodes_watched"] == 4


def test_two_titles_are_two_rows_even_with_the_same_name(exports):
    """MAL files each season as its own entry, and a franchise reuses its name."""
    rows = load(exports, entry(id="1", title="Same Name") + entry(id="2", title="Same Name"))
    assert len({r.id for r in rows}) == 2


def test_a_broken_export_yields_nothing_rather_than_half_a_list(exports):
    """A half-parsed list publishes a denominator for some titles and not
    others, which reads as "he never finished that one"."""
    (exports / "animelist-2026-06-05.xml").write_text("<myanimelist><anime>", encoding="utf-8")
    assert anime.load() == []


class TestMatchKey:
    """The only key t0_anime and t0_tv can be joined on."""

    def test_it_ignores_what_two_catalogues_disagree_about(self):
        assert match_key("The Difference Engine.") == match_key("The Difference Engine")
        assert match_key("Paper Lanterns & Rope") == match_key("Paper Lanterns and Rope")
        assert match_key("FLCL: Progressive") == match_key("flcl  progressive")
        assert match_key("Kaijū No. 8") == match_key("Kaiju No 8")

    def test_it_keeps_apart_what_they_agree_about(self):
        """A near miss costs a fallback; a false match costs the arithmetic.

        Season stripping is the tempting one and the one that would break it:
        MAL gives a second season its own total, so merging it with the first
        would divide one show's episode count by another show's length.
        """
        assert match_key("Wistoria: Wand and Sword") != match_key("Wistoria: Wand and Sword Season 2")
        assert match_key("Mission: Yozakura Family") != match_key("Mission: Yozakura-ke")

    def test_an_empty_title_makes_an_empty_key(self):
        """Which is why every join guards on `<> ''` rather than trusting it:
        two rows with no title are not the same show."""
        assert match_key("") == ""
        assert match_key("!!!") == ""


def test_both_zones_carry_the_key(exports, monkeypatch, tmp_path):
    """The join is a column comparison on the SQL side, so the key has to be
    ON the row in both zones rather than computed by whoever asks."""
    import json

    (exports / "trakt-cache.json").write_text(json.dumps({"shows": [{
        "last_watched_at": "2026-02-15T00:00:00.000Z", "plays": 4,
        "show": {"title": "The Difference Engine", "year": 2023,
                 "ids": {"trakt": 502, "slug": "the-difference-engine"}},
        "seasons": [{"number": 1, "episodes": [{"number": 1}, {"number": 2}]}],
    }]}), encoding="utf-8")

    watched = tv.load()
    listed = load(exports, entry(id="9002", title="The Difference Engine."))
    assert watched[0].payload["match_key"] == listed[0].payload["match_key"]


def test_the_key_is_not_part_of_any_id(exports, monkeypatch):
    """So it can be changed without re-minting a life's worth of rows."""
    original = one(exports, id="9001", title="A Show").id
    monkeypatch.setattr(anime, "match_key", lambda t: "something else entirely")
    assert one(exports, id="9001", title="A Show").id == original
