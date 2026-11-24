"""The Notion Reading Log fetcher and its overlay onto the Goodreads rows.

The property that matters is identity: `Row.id` hashes the payload, so a book
the owner did not touch must come back from Notion with the id it already had —
otherwise moving the shelf to Notion announces 994 books as "recently added".
"""
from __future__ import annotations

import json

import pytest

from exo.loaders import csv_sources
from exo.notes.sources.notion import api
from exo.provenance import Row
from exo.scripts_impl import _fetch, fetch_reading_log as rl


@pytest.fixture(autouse=True)
def exports(tmp_path, monkeypatch):
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(_fetch.config, "EXPORTS", d)
    monkeypatch.setattr(csv_sources.config, "EXPORTS", d)
    return d


def _page(title="Middlemarch", author="George Eliot", status="Read", rating=4,
          read="2024-01-05", added="2023-12-01", gid="19089", pid="abc-123"):
    def rt(s):
        return {"type": "rich_text", "rich_text": [{"plain_text": s, "type": "text",
                                                     "text": {"content": s}}]}
    return {
        "id": pid, "archived": False, "last_edited_time": "2026-11-24T10:00:00.000Z",
        "properties": {
            "Title": {"type": "title", "title": [{"plain_text": title}]},
            "Author": rt(author),
            "Status": {"type": "select", "select": {"name": status} if status else None},
            "Rating": {"type": "number", "number": rating},
            "Date read": {"type": "date", "date": {"start": read} if read else None},
            "Date added": {"type": "date", "date": {"start": added} if added else None},
            "Goodreads ID": rt(gid) if gid else {"type": "rich_text", "rich_text": []},
            "Notes": {"type": "rich_text", "rich_text": []},
        },
    }


def _gr_row(**over):
    payload = {"title": "Middlemarch", "book_author": "George Eliot", "my_rating": "4",
               "avg_rating": "3.99", "shelf": "read",
               "date_read": "2024-01-05T00:00:00", "date_added": "2023-12-01T00:00:00"}
    payload.update(over)
    return Row(tier="t0", zone="book", source="goodreads", author="external",
               created=payload["date_read"], origin_ref="19089", payload=payload)


# ─────────────────────────────── parse ───────────────────────────────


def test_parse_maps_status_to_the_goodreads_shelf_vocabulary():
    for status, shelf in [("Read", "read"), ("Reading", "currently-reading"),
                          ("To read", "to-read"), ("Partly read", "partly-read")]:
        assert rl.parse(_page(status=status))["shelf"] == shelf


def test_parse_writes_unrated_as_zero_like_goodreads():
    assert rl.parse(_page(rating=None))["my_rating"] == "0"
    assert rl.parse(_page(rating=5))["my_rating"] == "5"


def test_parse_skips_a_page_with_no_title():
    assert rl.parse(_page(title="")) is None


# ─────────────────────────────── identity ───────────────────────────────


def test_an_unedited_book_keeps_its_id():
    old = _gr_row()
    payload = rl.overlay(old.payload, rl.parse(_page()))
    assert payload == old.payload
    new = Row(tier="t0", zone="book", source="goodreads", author="external",
              created=old.created, origin_ref="19089", payload=payload)
    assert new.id == old.id


def test_a_real_edit_changes_only_the_edited_field():
    old = _gr_row()
    payload = rl.overlay(old.payload, rl.parse(_page(rating=5)))
    assert payload["my_rating"] == "5"
    assert payload["date_read"] == "2024-01-05T00:00:00"   # old spelling kept
    assert payload["avg_rating"] == "3.99"                  # Notion has none


def test_a_finished_book_moves_shelf_and_gains_its_date():
    old = _gr_row(shelf="currently-reading", date_read=None)
    payload = rl.overlay(old.payload, rl.parse(_page(read="2026-11-20")))
    assert payload["shelf"] == "read" and payload["date_read"] == "2026-11-20"


# ─────────────────────────────── loader ───────────────────────────────


def _cache(exports, entries):
    (exports / rl.CACHE).write_text(json.dumps({"books": entries}), encoding="utf-8")


def test_no_cache_means_the_goodreads_rows_unchanged(exports):
    rows = [_gr_row()]
    assert csv_sources._reading_log(rows) == rows


def test_the_loader_keeps_ids_and_adds_logged_books(exports):
    old = _gr_row()
    _cache(exports, [rl.parse(_page()),
                     rl.parse(_page(title="Piranesi", author="Susanna Clarke",
                                    gid="", pid="def-456", read="2026-11-01"))])
    out = csv_sources._reading_log([old])
    assert out[0].id == old.id
    logged = out[1]
    assert (logged.source, logged.origin_ref) == ("reading-log", "def-456")
    assert logged.payload["title"] == "Piranesi"


def test_a_goodreads_row_notion_does_not_mention_is_kept(exports):
    other = _gr_row()
    other.origin_ref = "999"
    _cache(exports, [rl.parse(_page(title="Something else", gid="", pid="p"))])
    assert other in csv_sources._reading_log([other])


# ─────────────────────────────── run ───────────────────────────────


def _stub(monkeypatch, pages):
    monkeypatch.setenv("READING_LOG_DATABASE_ID", "4223f93c-a2bc-8120-84e0-da9d33461db2")
    calls = []

    def paged(path, **kw):
        calls.append(path)
        return iter(pages)
    monkeypatch.setattr(api, "_paged", paged)
    return calls


def test_run_queries_the_dashless_database_and_writes_the_cache(exports, monkeypatch):
    calls = _stub(monkeypatch, [_page(), _page(title="Piranesi", gid="", pid="x")])
    assert rl.run() == 0
    assert calls == ["/databases/4223f93ca2bc812084e0da9d33461db2/query"]
    assert len(json.loads((exports / rl.CACHE).read_text())["books"]) == 2


def test_run_allows_a_small_delete_but_refuses_a_collapse(exports, monkeypatch):
    _cache(exports, [{"title": str(i)} for i in range(20)])
    _stub(monkeypatch, [_page(title=str(i), pid=str(i)) for i in range(19)])
    assert rl.run() == 0                      # 5% gone: an edit
    _stub(monkeypatch, [_page(title=str(i), pid=str(i)) for i in range(10)])
    assert rl.run() == 1                      # half gone: refused
    assert len(json.loads((exports / rl.CACHE).read_text())["books"]) == 19


def test_run_refuses_an_empty_query(exports, monkeypatch):
    _cache(exports, [{"title": "a"}])
    _stub(monkeypatch, [])
    assert rl.run() == 1


def test_run_without_a_database_id_says_so(monkeypatch, capsys):
    monkeypatch.delenv("READING_LOG_DATABASE_ID", raising=False)
    assert rl.run() == 1
    assert "READING_LOG_DATABASE_ID" in capsys.readouterr().out
