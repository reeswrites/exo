"""The three consumption fetchers ADR-0015 moved into the engine.

Everything here is about one property: these feeds are windows, not archives.
Letterboxd serves ~50 diary entries, Untappd ~25, and neither can page back. So
the cache file IS the history, and the tests that matter are the ones about not
losing it — a shrink refused, a parse-to-zero refused, a clock that cannot change
under an existing file.

No network. Each fetcher's parse is exercised against feed-shaped bytes and its
HTTP call is stubbed, because the failure these guard against is a markup change,
which is exactly what a live-feed test would not catch until it was too late.
"""
from __future__ import annotations

import json

import pytest

from exo.scripts_impl import (_fetch, fetch_lastfm, fetch_letterboxd,
                             fetch_letterboxd_avg, fetch_untappd)


@pytest.fixture(autouse=True)
def exports(tmp_path, monkeypatch):
    """Point the exports dir at a scratch directory for every test here."""
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(_fetch.config, "EXPORTS", d)
    monkeypatch.setattr(_fetch.config, "TIMEZONE", "UTC")
    return d


def _write(path, key, entries, **extra):
    path.write_text(json.dumps({**extra, key: entries}), encoding="utf-8")


# ───────────────────────────── the shrink guard ─────────────────────────────


def test_a_write_that_would_shrink_the_cache_is_refused(exports):
    with pytest.raises(_fetch.CacheRefused):
        _fetch.write_cache("c.json", "rows", [{"a": 1}], had=9)


def test_a_write_that_grows_or_holds_is_allowed(exports):
    _fetch.write_cache("c.json", "rows", [{"a": 1}, {"a": 2}], had=2)
    assert json.loads((exports / "c.json").read_text())["rows"] == [{"a": 1}, {"a": 2}]


def test_an_unparseable_cache_reads_empty_so_the_guard_refuses_the_write(exports):
    (exports / "c.json").write_text("{ this is not json", encoding="utf-8")
    entries, blob = _fetch.read_cache("c.json", "rows")
    # Empty, not an exception — but `had` is then 0, so the caller cannot tell
    # a corrupt file from a first run. That is why the fetchers refuse a
    # parse-to-zero feed separately: two independent ways to lose everything.
    assert entries == [] and blob == {}


# ─────────────────────────────── the clock ───────────────────────────────


def test_the_caches_own_timezone_wins_over_config(exports, monkeypatch):
    monkeypatch.setattr(_fetch.config, "TIMEZONE", "America/New_York")
    tz, name = _fetch.resolve_tz({"tz": "UTC"}, "c.json")
    assert name == "UTC", "an existing file must never be reinterpreted"


def test_config_pins_the_clock_when_the_cache_does_not_declare_one(exports, monkeypatch):
    monkeypatch.setattr(_fetch.config, "TIMEZONE", "America/New_York")
    _tz, name = _fetch.resolve_tz({}, "c.json")
    assert name == "America/New_York"


def test_an_unknown_timezone_refuses_rather_than_falling_back_to_local(exports, monkeypatch):
    monkeypatch.setattr(_fetch.config, "TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(_fetch.CacheRefused):
        _fetch.resolve_tz({}, "c.json")


def test_no_declared_zone_means_the_runners_zone_and_says_so(exports, monkeypatch, capsys):
    monkeypatch.setattr(_fetch.config, "TIMEZONE", "")
    tz, name = _fetch.resolve_tz({}, "c.json")
    assert tz is None and name == "local"
    assert "pin it" in capsys.readouterr().out


# ───────────────────────────────── letterboxd ─────────────────────────────────

LETTERBOXD = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com">
<channel>
  <item>
    <title>The Conversation, 1974 - the rating</title>
    <link>https://letterboxd.com/u/film/the-conversation/</link>
    <guid>letterboxd-watch-1</guid>
    <letterboxd:filmTitle>The Conversation</letterboxd:filmTitle>
    <letterboxd:filmYear>1974</letterboxd:filmYear>
    <letterboxd:watchedDate>2026-03-18</letterboxd:watchedDate>
    <letterboxd:memberRating>4.5</letterboxd:memberRating>
    <description>&lt;p&gt;A real review.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Stalker, 1979</title>
    <link>https://letterboxd.com/u/film/stalker/</link>
    <guid>letterboxd-watch-2</guid>
    <letterboxd:filmTitle>Stalker</letterboxd:filmTitle>
    <letterboxd:filmYear>1979</letterboxd:filmYear>
    <letterboxd:watchedDate>2026-03-19</letterboxd:watchedDate>
    <description>&lt;p&gt;Watched on Thursday March 19, 2026.&lt;/p&gt;</description>
  </item>
  <item>
    <title>A list of films</title>
    <link>https://letterboxd.com/u/list/best-of/</link>
    <guid>letterboxd-list-9</guid>
    <description>&lt;p&gt;Not a diary entry.&lt;/p&gt;</description>
  </item>
</channel>
</rss>"""


def test_letterboxd_reads_the_namespaced_fields_without_hardcoding_the_uri():
    got = fetch_letterboxd._entries(LETTERBOXD)
    assert [e["name"] for e in got] == ["The Conversation", "Stalker"]
    assert got[0]["year"] == "1974" and got[0]["date"] == "2026-03-18"


def test_letterboxd_skips_items_with_no_watched_date():
    assert all(e["name"] != "A list of films" for e in fetch_letterboxd._entries(LETTERBOXD))


def test_letterboxd_drops_the_watched_on_boilerplate_but_keeps_a_real_review():
    got = {e["name"]: e for e in fetch_letterboxd._entries(LETTERBOXD)}
    assert got["The Conversation"]["review"] == "A real review."
    assert "review" not in got["Stalker"], "boilerplate is not a review"


def test_letterboxd_omits_rating_rather_than_writing_zero():
    got = {e["name"]: e for e in fetch_letterboxd._entries(LETTERBOXD)}
    assert got["The Conversation"]["rating"] == "4.5"
    assert "rating" not in got["Stalker"], '"" and "0" are different claims'


def test_letterboxd_merges_onto_the_cache_and_dedupes_on_guid(exports, monkeypatch):
    _write(exports / "letterboxd-cache.json", "watched",
           [{"name": "The Conversation", "_guid": "letterboxd-watch-1"}])
    monkeypatch.setenv("LETTERBOXD_USER", "u")
    monkeypatch.setattr(_fetch, "get", lambda _url: LETTERBOXD)
    assert fetch_letterboxd.run() == 0
    got = json.loads((exports / "letterboxd-cache.json").read_text())["watched"]
    assert len(got) == 2, "the already-cached watch must not be added twice"


def test_letterboxd_treats_a_parse_to_zero_as_a_markup_change(exports, monkeypatch):
    _write(exports / "letterboxd-cache.json", "watched", [{"_guid": "a"}, {"_guid": "b"}])
    monkeypatch.setenv("LETTERBOXD_USER", "u")
    monkeypatch.setattr(_fetch, "get", lambda _url: b"<rss><channel></channel></rss>")
    assert fetch_letterboxd.run() == 1
    kept = json.loads((exports / "letterboxd-cache.json").read_text())["watched"]
    assert len(kept) == 2, "an empty feed must leave the history alone"


def test_letterboxd_leaves_the_cache_alone_when_the_feed_read_fails(exports, monkeypatch):
    _write(exports / "letterboxd-cache.json", "watched", [{"_guid": "a"}])
    monkeypatch.setenv("LETTERBOXD_USER", "u")

    def boom(_url):
        raise OSError("connection reset")

    monkeypatch.setattr(_fetch, "get", boom)
    assert fetch_letterboxd.run() == 1
    assert len(json.loads((exports / "letterboxd-cache.json").read_text())["watched"]) == 1


# ─────────────────────────────────── untappd ───────────────────────────────────

UNTAPPD = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Someone is drinking an Imperial Stout by Brewery Co at The Bar</title>
    <link>https://untappd.com/user/someone/checkin/1234567890</link>
    <pubDate>Sun, 31 May 2026 13:57:24 +0000</pubDate>
    <description>&lt;p&gt;Good.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Someone is drinking a Pilsner by Another Brewery</title>
    <link>https://untappd.com/user/someone/checkin/1234567891</link>
    <pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
    <description></description>
  </item>
</channel></rss>"""


def test_untappd_parses_the_beer_the_brewery_and_the_optional_venue():
    got = fetch_untappd._entries(UNTAPPD, None)
    assert got[0]["beer_name"] == "Imperial Stout"
    assert got[0]["brewery_name"] == "Brewery Co"
    assert got[0]["venue_name"] == "The Bar"
    assert "venue_name" not in got[1], "no venue clause means no venue, not empty"


def test_untappd_strips_the_article_from_the_beer_name():
    assert fetch_untappd._title("X is drinking an IPA by Y")[0] == "IPA"
    assert fetch_untappd._title("X is drinking a Pilsner by Y")[0] == "Pilsner"


def test_untappd_keeps_a_checkin_whose_title_will_not_parse():
    beer, brewery, venue = fetch_untappd._title("something else entirely")
    assert beer == "something else entirely" and brewery == "" and venue == ""


def test_untappd_renders_the_timestamp_in_the_pinned_zone():
    from zoneinfo import ZoneInfo
    got = fetch_untappd._entries(UNTAPPD, ZoneInfo("America/New_York"))
    # 13:57:24 +0000 is 09:57:24 in New York on that date.
    assert got[0]["created_at"] == "2026-05-31 09:57:24"


def test_untappd_takes_the_checkin_id_from_the_link():
    assert fetch_untappd._entries(UNTAPPD, None)[0]["checkin_id"] == "1234567890"


def test_untappd_carries_every_field_the_loader_reads():
    got = fetch_untappd._entries(UNTAPPD, None)[0]
    assert set(fetch_untappd.FIELDS) <= set(got)


def test_untappd_dedupes_against_both_spellings_of_the_id(exports, monkeypatch):
    # The blog's fetcher writes `_checkin_id`; the CSV seed writes `checkin_id`.
    _write(exports / "untappd-cache.json", "checkins",
           [{"checkin_id": "1234567890", "created_at": "2026-05-31 13:57:24"}])
    monkeypatch.setenv("UNTAPPD_USER", "u")
    monkeypatch.setenv("UNTAPPD_RSS_KEY", "k")
    monkeypatch.setattr(_fetch, "get", lambda _url: UNTAPPD)
    assert fetch_untappd.run() == 0
    got = json.loads((exports / "untappd-cache.json").read_text())["checkins"]
    assert len(got) == 2, "a row cached under the other spelling is still the same check-in"


# ─────────────────────────────────── lastfm ───────────────────────────────────


def _lastfm_page(tracks, total_pages=1):
    return json.dumps({"recenttracks": {
        "track": tracks, "@attr": {"totalPages": str(total_pages)}}}).encode()


TRACKS = [
    {"artist": {"#text": "Nowplaying Band"}, "album": {"#text": ""},
     "name": "Live", "@attr": {"nowplaying": "true"}},
    {"artist": {"#text": "Slowdive"}, "album": {"#text": "Souvlaki"},
     "name": "Alison", "date": {"uts": "1780000000"}},
    {"artist": {"#text": "Duster"}, "album": {"#text": "Stratosphere"},
     "name": "Constellations", "date": {"uts": "1780000600"}},
]


def test_lastfm_skips_the_nowplaying_entry(exports, monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USER", "u")
    monkeypatch.setattr(_fetch, "get", lambda _url: _lastfm_page(TRACKS))
    assert fetch_lastfm.run() == 0
    got = json.loads((exports / "lastfm-cache.json").read_text())["scrobbles"]
    assert [r["song"] for r in got] == ["Alison", "Constellations"]


def test_lastfm_dedupes_on_uts_and_writes_the_watermark(exports, monkeypatch):
    _write(exports / "lastfm-cache.json", "scrobbles",
           [{"artist": "Slowdive", "album": "Souvlaki", "song": "Alison",
             "scrobbled_at": "28 May 2026 20:26", "uts": 1780000000}],
           last_uts=1780000000, tz="UTC")
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USER", "u")
    monkeypatch.setattr(_fetch, "get", lambda _url: _lastfm_page(TRACKS))
    assert fetch_lastfm.run() == 0
    blob = json.loads((exports / "lastfm-cache.json").read_text())
    assert len(blob["scrobbles"]) == 2
    assert blob["last_uts"] == 1780000600


def test_lastfm_recomputes_the_watermark_rather_than_trusting_the_file(exports, monkeypatch):
    # A `last_uts` ahead of every row would skip the gap between the two,
    # permanently and with nothing to read.
    _write(exports / "lastfm-cache.json", "scrobbles",
           [{"song": "Alison", "uts": 1780000000}], last_uts=1799999999, tz="UTC")
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USER", "u")
    seen = {}

    def capture(url):
        seen["url"] = url
        return _lastfm_page(TRACKS)

    monkeypatch.setattr(_fetch, "get", capture)
    assert fetch_lastfm.run() == 0
    assert "from=1780000001" in seen["url"], "the watermark comes from the rows"


def test_lastfm_renders_the_timestamp_in_the_caches_own_zone(exports, monkeypatch):
    _write(exports / "lastfm-cache.json", "scrobbles", [], tz="America/New_York")
    monkeypatch.setattr(_fetch.config, "TIMEZONE", "UTC")
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USER", "u")
    monkeypatch.setattr(_fetch, "get", lambda _url: _lastfm_page(TRACKS[1:2]))
    assert fetch_lastfm.run() == 0
    blob = json.loads((exports / "lastfm-cache.json").read_text())
    assert blob["tz"] == "America/New_York"
    # 1780000000 is 2026-05-28 20:26 UTC, which is 16:26 the same day in New York.
    assert blob["scrobbles"][0]["scrobbled_at"] == "28 May 2026 16:26"


def test_lastfm_sorts_the_stream_oldest_first(exports, monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_USER", "u")
    monkeypatch.setattr(_fetch, "get", lambda _url: _lastfm_page(list(reversed(TRACKS))))
    assert fetch_lastfm.run() == 0
    got = json.loads((exports / "lastfm-cache.json").read_text())["scrobbles"]
    assert [r["uts"] for r in got] == sorted(r["uts"] for r in got)


def test_a_missing_credential_says_which_one(exports, monkeypatch, capsys):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.setenv("LASTFM_USER", "u")
    assert fetch_lastfm.run() == 1
    assert "LASTFM_API_KEY" in capsys.readouterr().out


# ─────────────────────────── letterboxd averages ────────────────────────────
#
# A different property from the feed fetchers above. Film pages are permanent,
# so nothing here is about losing a window — it is about not re-fetching six
# hundred pages, and about the join key matching the one the loader merges on.

FILM_PAGE = b"""<html><head>
<script type="application/ld+json">
/* <![CDATA[ */
{"@type":"Movie","name":"Stalker",
 "aggregateRating":{"@type":"AggregateRating","ratingValue":4.28,"bestRating":5}}
/* ]]> */
</script></head><body></body></html>"""

UNRATED_PAGE = b"""<html><head>
<script type="application/ld+json">
/* <![CDATA[ */
{"@type":"Movie","name":"Some Short Nobody Rated"}
/* ]]> */
</script></head><body></body></html>"""


def test_the_average_is_read_through_letterboxds_cdata_wrapper(monkeypatch):
    monkeypatch.setattr(_fetch, "get", lambda _url: FILM_PAGE)
    assert fetch_letterboxd_avg._average("https://boxd.it/x") == 4.28


def test_a_page_with_no_aggregate_rating_is_none_not_zero(monkeypatch):
    monkeypatch.setattr(_fetch, "get", lambda _url: UNRATED_PAGE)
    assert fetch_letterboxd_avg._average("https://boxd.it/x") is None


def test_the_cache_key_is_the_one_the_loader_merges_on():
    # csv_sources._film_key lowercases the title and strips both halves. A key
    # that drifts from it turns the join into a silent miss rather than an error.
    assert fetch_letterboxd_avg._key("  Stalker ", " 1979 ") == "stalker|1979"
    assert fetch_letterboxd_avg._key("Stalker", None) == "stalker|"


def test_already_cached_films_are_not_fetched_again(exports, monkeypatch, tmp_path):
    _write(exports / "letterboxd-cache.json", "watched",
           [{"name": "Stalker", "year": "1979", "letterboxd_uri": "https://boxd.it/a"},
            {"name": "Solaris", "year": "1972", "letterboxd_uri": "https://boxd.it/b"}])
    _write(exports / "letterboxd-avg-cache.json", "films",
           [{"_key": "stalker|1979", "title": "Stalker", "year": "1979",
             "avg_rating": 4.28, "uri": "https://boxd.it/a"}])
    monkeypatch.setattr(fetch_letterboxd_avg.config, "EXPORTS", exports)
    monkeypatch.setattr(fetch_letterboxd_avg.config, "latest", lambda _pat: None)
    monkeypatch.setattr(fetch_letterboxd_avg, "DELAY", 0)

    asked = []
    monkeypatch.setattr(_fetch, "get", lambda url: asked.append(url) or FILM_PAGE)
    assert fetch_letterboxd_avg.run() == 0

    assert asked == ["https://boxd.it/b"], "the cached film must not be refetched"
    got = json.loads((exports / "letterboxd-avg-cache.json").read_text())["films"]
    assert len(got) == 2


def test_an_unrated_film_is_cached_so_reruns_stop_chasing_it(exports, monkeypatch):
    _write(exports / "letterboxd-cache.json", "watched",
           [{"name": "Some Short", "year": "2001", "letterboxd_uri": "https://boxd.it/c"}])
    monkeypatch.setattr(fetch_letterboxd_avg.config, "EXPORTS", exports)
    monkeypatch.setattr(fetch_letterboxd_avg.config, "latest", lambda _pat: None)
    monkeypatch.setattr(fetch_letterboxd_avg, "DELAY", 0)
    monkeypatch.setattr(_fetch, "get", lambda _url: UNRATED_PAGE)
    assert fetch_letterboxd_avg.run() == 0

    got = json.loads((exports / "letterboxd-avg-cache.json").read_text())["films"]
    assert got[0]["avg_rating"] is None, "an unrated film is a fact, not a gap"

    # And a second run leaves it alone rather than fetching it forever.
    asked = []
    monkeypatch.setattr(_fetch, "get", lambda url: asked.append(url) or UNRATED_PAGE)
    assert fetch_letterboxd_avg.run() == 0
    assert asked == []


def test_one_films_failure_does_not_end_the_run(exports, monkeypatch):
    _write(exports / "letterboxd-cache.json", "watched",
           [{"name": "Bad", "year": "1", "letterboxd_uri": "https://boxd.it/bad"},
            {"name": "Good", "year": "2", "letterboxd_uri": "https://boxd.it/good"}])
    monkeypatch.setattr(fetch_letterboxd_avg.config, "EXPORTS", exports)
    monkeypatch.setattr(fetch_letterboxd_avg.config, "latest", lambda _pat: None)
    monkeypatch.setattr(fetch_letterboxd_avg, "DELAY", 0)

    def flaky(url):
        if url.endswith("bad"):
            raise OSError("connection reset")
        return FILM_PAGE
    monkeypatch.setattr(_fetch, "get", flaky)
    assert fetch_letterboxd_avg.run() == 0

    got = json.loads((exports / "letterboxd-avg-cache.json").read_text())["films"]
    assert [e["title"] for e in got] == ["Good"]
