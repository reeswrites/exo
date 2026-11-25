"""The director: read off the film page, backfilled, and kept out of the id.

`ratings(director:)` answers "which Ken Russell films have I seen". The id rule
is the one that matters most: film ids hash the payload, and a backfilled name
must not re-mint 720 films as newly seen.
"""
from __future__ import annotations

import json

import pytest

from exo.loaders import csv_sources
from exo.provenance import Row
from exo.scripts_impl import _fetch, fetch_letterboxd_avg as avg

PAGE = b"""<script type="application/ld+json">
/* <![CDATA[ */
{"@type":"Movie","name":"Women in Love",
 "director":[{"@type":"Person","name":"Ken Russell"}],
 "aggregateRating":{"ratingValue":3.6}}
/* ]]> */
</script>"""
TWO = PAGE.replace(b'[{"@type":"Person","name":"Ken Russell"}]',
                   b'[{"name":"Joel Coen"},{"name":"Ethan Coen"}]')


@pytest.fixture
def exports(tmp_path, monkeypatch):
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(_fetch.config, "EXPORTS", d)
    monkeypatch.setattr(csv_sources.config, "EXPORTS", d)
    monkeypatch.setattr(avg.time, "sleep", lambda _s: None)
    return d


def test_director_is_read_from_the_page(monkeypatch):
    monkeypatch.setattr(_fetch, "get", lambda _u: PAGE)
    assert avg._page("https://boxd.it/x") == (3.6, "Ken Russell")
    monkeypatch.setattr(_fetch, "get", lambda _u: TWO)
    assert avg._page("https://boxd.it/x")[1] == "Joel Coen, Ethan Coen"


def test_entries_cached_before_directors_are_backfilled(monkeypatch, exports):
    (exports / avg.CACHE).write_text(json.dumps({"films": [
        {"_key": "women in love|1969", "title": "Women in Love", "year": "1969",
         "avg_rating": 3.6, "uri": "https://boxd.it/x"}]}))
    monkeypatch.setattr(avg, "_films", lambda: [("Women in Love", "1969", "https://boxd.it/x")])
    monkeypatch.setattr(_fetch, "get", lambda _u: PAGE)
    assert avg.run() == 0
    films = json.loads((exports / avg.CACHE).read_text())["films"]
    assert films[0]["director"] == "Ken Russell" and len(films) == 1
    # A second run has nothing left to backfill and fetches nothing.
    monkeypatch.setattr(_fetch, "get", lambda _u: pytest.fail("refetched"))
    assert avg.run() == 0


def test_the_director_does_not_change_a_films_id(monkeypatch, exports, tmp_path):
    csv_path = tmp_path / "letterboxd_ratings.csv"
    csv_path.write_text("Date,Name,Year,Letterboxd URI,Rating\n"
                        "2024-01-05,Women in Love,1969,https://boxd.it/x,4\n")
    monkeypatch.setattr(csv_sources.config, "latest", lambda _p: csv_path)
    before = csv_sources.letterboxd()[0]
    assert before.payload["director"] == ""
    (exports / avg.CACHE).write_text(json.dumps({"films": [
        {"_key": "women in love|1969", "avg_rating": None, "director": "Ken Russell"}]}))
    after = csv_sources.letterboxd()[0]
    assert after.payload["director"] == "Ken Russell"
    assert after.id == before.id
    # ...and it is the id the loader minted before the field existed at all.
    old = Row(tier="t0", zone="film", source="letterboxd", author="external",
              created=before.created, origin_ref=before.origin_ref,
              payload={k: v for k, v in before.payload.items() if k != "director"})
    assert before.id == old.id
