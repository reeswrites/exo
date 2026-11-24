"""`t0_music_day` answers what the t0_music scans answered, without the scans.

The read surface's music aggregates — `consumption` and `medium` (a count and a
last date), `around_the_time` (artists and plays in a window, and its top five)
— each read all ~44k scrobbles per call. On 2026-11-24 they spent over a million
of D1's five-million-row daily budget and the nightly could not publish. They
now read a per-day rollup the bundle emits beside the stream.

The point of these tests is the word EXACTLY. Every assertion runs the OLD
statement against the published stream and the NEW one against the published
rollup, in the same SQLite database, over the same rows, and compares. The new
SQL is lifted out of worker/src/tools.js rather than restated here, so the test
checks the statements the worker will run and not a copy of them.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import duckdb
import pytest

from exo.scripts_impl import publish_cf

TOOLS_JS = Path(__file__).resolve().parent.parent / "worker/src/tools.js"


def _const(name: str) -> str:
    src = TOOLS_JS.read_text()
    m = re.search(rf"const {name} =\s*(\"(?:[^\"\\]|\\.)*\"|`[^`]*`);", src)
    assert m, f"could not read {name} out of worker/src/tools.js"
    return m.group(1)[1:-1]


MUSIC_TOTAL = _const("MUSIC_TOTAL")
MUSIC_WINDOW_ARTISTS = _const("MUSIC_WINDOW_ARTISTS")

# A stream with every shape that could make a rollup disagree with a scan:
# case-distinct artists (count DISTINCT is case-sensitive), a NULL artist, a
# scrobble with no `created`, quotes and non-ASCII in names, ties in plays, an
# artist who spans days, and timestamps whose first ten characters are the day.
STREAM = [
    ("2026-03-01T09:00:00", "Low", "Things We Lost", "Sunflower"),
    ("2026-03-01T09:05:00", "Low", "Things We Lost", "Laser"),
    ("2026-03-01T23:59:59", "low", "a lower-case act", "x"),
    ("2026-03-02T00:00:00", "Sigur Rós", "( )", "Untitled 1"),
    ("2026-03-02T01:00:00", "Sinéad O'Connor", "Lion", "Jackie"),
    ("2026-03-02T01:10:00", "Sinéad O'Connor", "Lion", "Mandinka"),
    ("2026-03-02T02:00:00", None, None, "an untagged scrobble"),
    ("2026-03-15T12:00:00", "Low", "Double Negative", "Quorum"),
    ("2026-03-15T12:04:00", "Sigur Rós", "Takk", "Glósóli"),
    ("2026-03-31T22:00:00", "Grouper", "Ruins", "Holofernes"),
    ("2026-04-01T00:00:01", "Grouper", "Ruins", "Clearing"),
    ("2026-04-01T00:10:00", "Low", "HEY WHAT", "White Horses"),
    ("2026-04-02T10:00:00", "1975", "a numeric-looking name", "y"),
    (None, "Grouper", "Ruins", "a scrobble with no time"),
]

WINDOWS = [
    ("2026-03-01", "2026-03-31"),
    ("2026-03-01", "2026-03-01"),
    ("2026-03-02", "2026-03-02"),
    ("2026-03-02", "2026-04-01"),
    ("2026-01-01", "2026-12-31"),
    ("2025-01-01", "2025-12-31"),       # nothing in it
    ("2026-04-02", "2026-03-01"),       # backwards: empty, both ways
    ("2026-03-15T00:00", "2026-04-01"),  # a caller passing a timestamp
]


@pytest.fixture
def d1(tmp_path):
    """The bundle's t0_music and t0_music_day, loaded the way import.sh loads
    them — the emitted DDL, then the emitted data — into one SQLite database."""
    parquet = tmp_path / "t0_music.parquet"
    con = duckdb.connect()
    con.execute("CREATE TABLE s (id VARCHAR, created VARCHAR, artist VARCHAR, "
                "album VARCHAR, track VARCHAR)")
    con.executemany("INSERT INTO s VALUES (?, ?, ?, ?, ?)",
                    [(f"m{i}", *r) for i, r in enumerate(STREAM)])
    con.execute(f"COPY s TO '{parquet}' (FORMAT parquet)")

    out = tmp_path / "data"
    ddl = tmp_path / "ddl"
    out.mkdir()
    ddl.mkdir()
    publish_cf._emit_table(con, parquet, "t0_music", out, ddl_dir=ddl)
    _, info = publish_cf._emit_music_day(con, parquet, out, ddl_dir=ddl)
    con.close()

    db = sqlite3.connect(":memory:")
    for t in ("t0_music", "t0_music_day"):
        db.executescript((ddl / f"{t}.sql").read_text())
        db.executescript((out / f"{t}.sql").read_text())
    db.row_factory = sqlite3.Row
    yield db, info, parquet
    db.close()


def _rows(db, sql, *binds):
    return [tuple(r) for r in db.execute(sql, binds).fetchall()]


def test_the_total_and_last_date_match_the_scan(d1):
    db, _, _ = d1
    old = _rows(db, "SELECT count(*) AS n, max(substr(created,1,10)) AS last_logged "
                    "FROM t0_music")
    assert _rows(db, MUSIC_TOTAL) == old
    assert old == [(len(STREAM), "2026-04-02")]


@pytest.mark.parametrize("window", WINDOWS)
def test_a_windows_artists_and_plays_match_the_scan(d1, window):
    db, _, _ = d1
    w = "substr(created,1,10) BETWEEN ? AND ?"
    old = _rows(db, f"SELECT (SELECT count(DISTINCT artist) FROM t0_music WHERE {w}), "
                    f"(SELECT count(*) FROM t0_music WHERE {w})", *window, *window)
    new = _rows(db, f"SELECT (SELECT count(artist) FROM ({MUSIC_WINDOW_ARTISTS})), "
                    "(SELECT COALESCE(sum(plays), 0) FROM t0_music_day "
                    "WHERE day BETWEEN ? AND ?)", *window, *window)
    assert new == old


@pytest.mark.parametrize("window", WINDOWS)
def test_a_windows_top_artists_match_the_scan(d1, window):
    # The old statement ordered by plays alone, so a tie came back in whatever
    # order the sorter left it. The new one breaks ties by name; the comparison
    # gives the old one the same tiebreak, so this checks the counts and the
    # membership and not an accident of SQLite's sort.
    db, _, _ = d1
    old = _rows(db, "SELECT 'artist', artist, count(*) AS plays FROM t0_music "
                    "WHERE substr(created,1,10) BETWEEN ? AND ? "
                    "GROUP BY artist ORDER BY plays DESC, artist LIMIT 5", *window)
    new = _rows(db, f"SELECT 'artist', artist, plays FROM ({MUSIC_WINDOW_ARTISTS}) "
                    "ORDER BY plays DESC, artist LIMIT 5", *window)
    assert new == old


def test_the_whole_window_sees_every_artist_including_case_variants(d1):
    db, _, _ = d1
    [(n,)] = _rows(db, f"SELECT count(artist) FROM ({MUSIC_WINDOW_ARTISTS})",
                   "0000-00-00", "9999-99-99")
    # Low, low, Sigur Rós, Sinéad O'Connor, Grouper, 1975 — the NULL artist and
    # the timeless scrobble are in the total, not in any window's artist count.
    assert n == 6


def test_a_numeric_looking_artist_stays_text(d1):
    # A name like "1975" must come back out of the JSON as the same TEXT the
    # stream holds, or a join or a comparison on it would quietly stop matching.
    db, _, _ = d1
    got = _rows(db, f"SELECT artist, typeof(artist) FROM ({MUSIC_WINDOW_ARTISTS})",
                "2026-04-02", "2026-04-02")
    assert got == [("1975", "text")]


def test_one_row_per_day_with_the_timeless_scrobbles_in_a_null_day(d1):
    db, info, _ = d1
    days = _rows(db, "SELECT day, plays FROM t0_music_day ORDER BY day")
    assert days[0] == (None, 1)
    assert len(days) == info["rows"] == len({r[0] and r[0][:10] for r in STREAM})
    assert sum(p for _, p in days) == len(STREAM)


def test_the_day_is_indexed(d1):
    db, _, _ = d1
    plan = " ".join(str(tuple(r)) for r in db.execute(
        "EXPLAIN QUERY PLAN SELECT plays FROM t0_music_day "
        "WHERE day BETWEEN '2026-03-01' AND '2026-03-31'"))
    assert "idx_t0_music_day_day" in plan, plan


def test_an_unchanged_stream_republishes_an_identical_digest(d1, tmp_path):
    # The load skips a table whose digest matches (ADR-0026). A rollup that
    # rendered its JSON in a different order each time would reload every night.
    _, info, parquet = d1
    con = duckdb.connect()
    again = tmp_path / "again"
    again.mkdir()
    _, info2 = publish_cf._emit_music_day(con, parquet, again)
    con.close()
    assert info2["digest"] == info["digest"]
    assert info2["rows"] == info["rows"]


def test_an_envelope_only_stream_rolls_up_to_nothing(tmp_path):
    parquet = tmp_path / "t0_music.parquet"
    con = duckdb.connect()
    con.execute("CREATE TABLE s (id VARCHAR, created VARCHAR)")
    con.execute(f"COPY s TO '{parquet}' (FORMAT parquet)")
    _, info = publish_cf._emit_music_day(con, parquet, tmp_path)
    con.close()
    assert info["rows"] == 0


# ─────────────── the rollup travels with the stream, and only with it ───────────────


def _projection(tmp_path, monkeypatch, zones, scope=None):
    serve = tmp_path / "_serve"
    serve.mkdir()
    con = duckdb.connect()
    for z in zones:
        con.execute(f"COPY (SELECT 'x' AS id, '2026-03-01T00:00:00' AS created, "
                    f"'Low' AS artist) TO '{serve / (z + '.parquet')}' (FORMAT parquet)")
    con.close()
    if scope:
        (serve / "_scope.json").write_text(json.dumps(scope), encoding="utf-8")
    monkeypatch.setattr(publish_cf.config, "SERVE", serve)
    return serve / "cf"


def test_a_full_bundle_carries_the_rollup_beside_the_stream(tmp_path, monkeypatch):
    out = _projection(tmp_path, monkeypatch, ["t0_music", "t1_notes"])
    assert publish_cf.run() == 0
    tables = (out / "served-tables.txt").read_text().split()
    assert "t0_music_day" in tables
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["d1_tables"]["t0_music_day"] == 1
    assert "t0_music_day" in (out / "expected-counts.txt").read_text()
    assert (out / "ddl" / "t0_music_day.sql").exists()


def test_a_lane_that_rebuilt_the_stream_ships_its_rollup(tmp_path, monkeypatch):
    out = _projection(tmp_path, monkeypatch, ["t0_music", "t1_notes"],
                      scope={"scope": "partial", "rebuilt": ["t0_music"]})
    assert publish_cf.run() == 0
    assert (out / "bundle-tables.txt").read_text().split() == ["t0_music", "t0_music_day"]


def test_a_lane_that_did_not_touch_the_stream_ships_no_rollup(tmp_path, monkeypatch):
    # A rollup from carried-over scrobbles would push last night's counts over
    # whatever the music lane loaded an hour ago — the same rule as the stream.
    out = _projection(tmp_path, monkeypatch, ["t0_music", "t1_notes"],
                      scope={"scope": "partial", "rebuilt": ["t1_notes"]})
    assert publish_cf.run() == 0
    assert (out / "bundle-tables.txt").read_text().split() == ["t1_notes"]


def test_no_stream_no_rollup(tmp_path, monkeypatch):
    # A held t0_music is absent from the projection; its counts must not leave
    # by another name.
    out = _projection(tmp_path, monkeypatch, ["t1_notes"])
    assert publish_cf.run() == 0
    assert "t0_music_day" not in (out / "served-tables.txt").read_text().split()
