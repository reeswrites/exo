"""album_pool: a record already heard must never come back as unheard."""
import json

from exo import config
from exo.loaders import album_pool
from exo.provenance import Row
from exo.scripts_impl import fetch_album_pool as fap


def test_loose_forgives_editions_and_punctuation():
    base = album_pool.loose("Bladee", "Cold Visions")
    assert album_pool.loose("bladee", "Cold Visions (Deluxe)") == base
    assert album_pool.loose("Bladee", "Cold Visions [Explicit]") == base
    assert album_pool.loose("Aries", "WELCOME HOME - EP") == album_pool.loose("Aries", "Welcome Home")
    assert album_pool.loose("Bladee", "Crest") != base


def _scrobbles(monkeypatch, pairs):
    from exo.loaders import csv_sources
    monkeypatch.setattr(csv_sources, "lastfm", lambda: [
        Row(tier="t0", zone="music", source="lastfm", author="external",
            payload={"artist": a, "album": b, "track": "t"}) for a, b in pairs])


def test_loader_drops_heard_merges_sources_and_attaches_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS", tmp_path)
    _scrobbles(monkeypatch, [("Bladee", "Cold Visions")])
    (tmp_path / fap.CACHE).write_text(json.dumps({"entries": [
        {"key": "catalog\x1fbladee", "kind": "catalog", "name": "Bladee", "asked": "2026-09-29",
         "albums": [{"artist": "Bladee", "album": "Cold Visions (Deluxe)", "rank": 1},
                    {"artist": "Bladee", "album": "Crest", "rank": 2}]},
        {"key": "scene\x1frage", "kind": "scene", "name": "rage", "asked": "2026-09-29",
         "albums": [{"artist": "Bladee", "album": "Crest", "rank": 9},
                    {"artist": "Summrs", "album": "Fallen Raven", "rank": 5}]},
        {"key": "bladee\x1fcrest", "kind": "tags", "name": "Bladee", "album": "Crest",
         "status": "ok", "tags": ["drain", "2022"], "asked": "2026-09-29"},
    ]}))
    rows = {r.payload["album"]: r.payload for r in album_pool.load()}
    assert "Cold Visions (Deluxe)" not in rows, "a heard record leaked into the pool"
    assert rows["Crest"]["found_via"] == "back_catalog, scene"
    assert rows["Crest"]["scenes"] == "rage"
    assert rows["Crest"]["rank"] == 2
    assert rows["Crest"]["tags"] == "drain"          # the year is noise
    assert rows["Fallen Raven"]["tag_status"] == "pending"
    assert rows["Fallen Raven"]["artist_key"] == "summrs"


def test_pool_rows_are_not_grounds(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS", tmp_path)
    _scrobbles(monkeypatch, [])
    (tmp_path / fap.CACHE).write_text(json.dumps({"entries": [
        {"key": "scene\x1frage", "kind": "scene", "name": "rage",
         "albums": [{"artist": "A", "album": "B", "rank": 1}]}]}))
    (row,) = album_pool.load()
    assert row.grounds is False and row.author == "external"


def test_artist_plays_counts_every_scrobble_on_the_worker_fold(tmp_path, monkeypatch):
    # The worker no longer counts plays per call — this column is the whole answer.
    monkeypatch.setattr(config, "EXPORTS", tmp_path)
    _scrobbles(monkeypatch, [("Bladee", "Cold Visions"), ("bladee", ""), ("Bladee ", "Crest"),
                             ("Ecco2k", "E")])
    (tmp_path / fap.CACHE).write_text(json.dumps({"entries": [
        {"key": "scene\x1frage", "kind": "scene", "name": "rage",
         "albums": [{"artist": "Bladee", "album": "Exeter", "rank": 1},
                    {"artist": "Summrs", "album": "Fallen Raven", "rank": 2}]}]}))
    rows = {r.payload["album"]: r.payload for r in album_pool.load()}
    assert rows["Exeter"]["artist_plays"] == 3, "album-less and case/space variants all count"
    assert rows["Fallen Raven"]["artist_plays"] == 0
