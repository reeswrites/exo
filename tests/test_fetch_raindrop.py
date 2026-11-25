"""The Raindrop API fetcher, and the loader preferring its cache to the snapshot.

The snapshot froze on the day it was pulled and never carried the owner's notes
or the page excerpts. The cache must be preferred whenever it exists, keep both
fields, name nested collections, and refuse a run that lost most of the library.
"""
from __future__ import annotations

import json

import pytest

from exo.loaders import raindrop
from exo.scripts_impl import _fetch, fetch_raindrop as fr


@pytest.fixture(autouse=True)
def exports(tmp_path, monkeypatch):
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(_fetch.config, "EXPORTS", d)
    monkeypatch.setattr(raindrop.config, "EXPORTS", d)
    monkeypatch.setattr(raindrop, "_SNAPSHOT", tmp_path / "no-snapshot.json")
    monkeypatch.setenv("RAINDROP_TOKEN", "test-token")
    return d


def _item(i, note="", excerpt="", coll=7):
    return {"_id": i, "title": f"save {i}", "type": "article", "link": f"https://e.com/{i}",
            "created": "2026-11-01T10:00:00.000Z", "tags": ["a", "b"],
            "collection": {"$id": coll}, "note": note, "excerpt": excerpt}


def _api(items, cols=None, children=None):
    pages = [items[i:i + fr.PER_PAGE] for i in range(0, len(items), fr.PER_PAGE)]

    def get(path, token):
        assert token == "test-token"
        if path == "/collections":
            return {"items": cols or [{"_id": 7, "title": "thought-provoking"}]}
        if path == "/collections/childrens":
            return {"items": children or []}
        page = int(path.split("&page=")[1].split("&")[0])
        return {"items": pages[page] if page < len(pages) else []}
    return get


def test_fetch_keeps_notes_excerpts_and_nested_collections(monkeypatch, exports):
    monkeypatch.setattr(fr, "_get", _api(
        [_item(1, note="why I kept it", excerpt="the page says"), _item(2, coll=9)],
        children=[{"_id": 9, "title": "yummy"}]))
    assert fr.run() == 0
    rows = raindrop.load()
    by = {r.origin_ref: r.payload for r in rows}
    assert by["1"]["note"] == "why I kept it" and by["1"]["excerpt"] == "the page says"
    assert by["2"]["collection"] == "yummy"
    assert by["1"]["tags"] == "a, b"


def test_cache_is_preferred_to_the_snapshot(tmp_path, monkeypatch, exports):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"collections": {}, "bookmarks": [
        {"bookmark_id": 99, "title": "old", "link": "https://old"}]}))
    monkeypatch.setattr(raindrop, "_SNAPSHOT", snap)
    monkeypatch.delenv("RAINDROP_TOKEN")
    assert [r.origin_ref for r in raindrop.load()] == ["99"]
    monkeypatch.setenv("RAINDROP_TOKEN", "test-token")
    monkeypatch.setattr(fr, "_get", _api([_item(1)]))
    fr.run()
    monkeypatch.delenv("RAINDROP_TOKEN")
    assert [r.origin_ref for r in raindrop.load()] == ["1"]


def test_paging_walks_past_the_first_page(monkeypatch):
    monkeypatch.setattr(fr, "_get", _api([_item(i) for i in range(120)]))
    assert fr.run() == 0
    assert len(raindrop.load()) == 120


def test_a_large_drop_is_refused_and_the_cache_kept(monkeypatch, exports):
    monkeypatch.setattr(fr, "_get", _api([_item(i) for i in range(100)]))
    fr.run()
    monkeypatch.setattr(fr, "_get", _api([_item(i) for i in range(50)]))
    assert fr.run() == 1
    assert len(raindrop.load()) == 100
    # A small drop is an edit, and is written.
    monkeypatch.setattr(fr, "_get", _api([_item(i) for i in range(95)]))
    assert fr.run() == 0
    assert len(raindrop.load()) == 95


def test_no_token_writes_nothing(monkeypatch, exports):
    monkeypatch.delenv("RAINDROP_TOKEN")
    assert fr.run() == 1
    assert not (exports / fr.CACHE).exists()
