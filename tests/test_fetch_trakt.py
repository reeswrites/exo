"""`fetch-trakt`'s write guard and file shape.

`/sync/watched/shows` is the whole history, so the cache can only grow. A
smaller or empty 200 means something went wrong, not that history was deleted,
and writing it would wipe the record. No network here: the guard is a pure
function of what the cache held before.
"""
from __future__ import annotations

import json

import pytest

from exo.scripts_impl import fetch_trakt


def _show(n: int) -> dict:
    return {"plays": 1, "last_watched_at": f"2026-11-0{n}T00:00:00.000Z",
            "show": {"title": f"Shöw {n}", "ids": {"trakt": n, "slug": f"s{n}"}},
            "seasons": [{"number": 1, "episodes": [{"number": 1}]}]}


def test_an_empty_fetch_is_refused(tmp_path):
    with pytest.raises(ValueError, match="nothing fetched"):
        fetch_trakt.write_cache(tmp_path / "c.json", "", [], had=0)
    assert not (tmp_path / "c.json").exists()


def test_a_shrunken_fetch_is_refused_and_the_cache_kept(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="shrunken"):
        fetch_trakt.write_cache(path, "x", [_show(1)], had=2)
    assert path.read_text(encoding="utf-8") == "keep me"


def test_a_grown_fetch_writes_the_blog_fetchers_exact_bytes(tmp_path):
    path = tmp_path / "c.json"
    shows = [_show(1), _show(2)]
    fetch_trakt.write_cache(path, "2026-11-02T00:00:00.000Z", shows, had=1)
    want = json.dumps({"watched_at": "2026-11-02T00:00:00.000Z", "shows": shows},
                      indent=4, ensure_ascii=False)
    assert path.read_text(encoding="utf-8") == want


def test_an_unparseable_cache_blocks_the_write(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{ not json", encoding="utf-8")
    _shows, _at, readable = fetch_trakt._read_cache(path)
    assert readable is False


def test_seasons_rebuilt_from_history():
    from exo.scripts_impl.fetch_trakt import seasons_from_history

    def play(show, s, e, at):
        return {"show": {"ids": {"trakt": show}},
                "episode": {"season": s, "number": e}, "watched_at": at}

    got = seasons_from_history([
        play(1, 1, 2, "2026-01-02"), play(1, 1, 1, "2026-01-01"),
        play(1, 1, 1, "2026-03-01"), play(1, 2, 1, "2026-02-01"),
        play(2, 1, 1, "2026-01-05"),
    ])
    assert got[1] == [
        {"number": 1, "episodes": [
            {"number": 1, "plays": 2, "last_watched_at": "2026-03-01"},
            {"number": 2, "plays": 1, "last_watched_at": "2026-01-02"}]},
        {"number": 2, "episodes": [
            {"number": 1, "plays": 1, "last_watched_at": "2026-02-01"}]},
    ]
    assert got[2][0]["episodes"][0]["plays"] == 1
