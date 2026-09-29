"""music_tag: the join key must fold as SQLite folds, and noise must not reach D1."""
import json

from exo import config
from exo.loaders import music_tags
from exo.scripts_impl import fetch_music_tags as fmt


def test_fold_matches_sqlite_lower_trim():
    # SQLite's lower() is ASCII-only and trim() strips spaces only. A Python
    # .lower() would fold the É and the join to the scrobbles would miss.
    assert music_tags.fold("  Éthiopiques Vol. 4 ") == "Éthiopiques vol. 4"
    assert music_tags.fold("BLADEE") == "bladee"
    assert music_tags.fold("\tx") == "\tx"


def test_noise_is_dropped_but_sound_words_are_kept():
    assert fmt.is_noise("2025")
    assert fmt.is_noise("seen live")
    assert fmt.is_noise("bladee", "Bladee")
    assert not fmt.is_noise("rage")
    assert not fmt.is_noise("2000s")   # a decade is a sound; a year is a date


def _cache(tmp_path, monkeypatch, entries):
    monkeypatch.setattr(config, "EXPORTS", tmp_path)
    (tmp_path / fmt.CACHE).write_text(json.dumps({"entries": entries}))


def test_loader_rows_carry_level_status_and_folded_keys(tmp_path, monkeypatch):
    _cache(tmp_path, monkeypatch, [
        {"key": "k1", "artist": "Bladee", "album": "Cold Visions", "status": "ok",
         "tags": ["rage", "2024", "trap"], "asked": "2026-09-28"},
        {"key": "k2", "artist": "Bladee", "album": "", "status": "ok",
         "tags": ["cloud rap"], "asked": "2026-09-28"},
        # Every tag noise: asked-and-answered-no once cleaned.
        {"key": "k3", "artist": "X", "album": "Y", "status": "ok",
         "tags": ["2021", "aoty"], "asked": "2026-09-28"},
    ])
    rows = {r.payload["album"]: r.payload for r in music_tags.load()}
    assert rows["Cold Visions"]["tags"] == "rage, trap"
    assert rows["Cold Visions"]["level"] == "album"
    assert rows["Cold Visions"]["artist_key"] == "bladee"
    assert rows["Cold Visions"]["album_key"] == "cold visions"
    assert rows[""]["level"] == "artist"
    assert rows["Y"]["status"] == "none" and rows["Y"]["tags"] == ""


def test_loader_rows_are_not_grounds(tmp_path, monkeypatch):
    _cache(tmp_path, monkeypatch, [
        {"key": "k", "artist": "A", "album": "", "status": "ok", "tags": ["emo"]}])
    (row,) = music_tags.load()
    assert row.grounds is False and row.author == "external"
