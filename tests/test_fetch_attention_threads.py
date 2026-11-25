"""Attention threads: the Notion fetcher, the loader, and the held-title rule.

What `recent_topics` reads now that the chat exports have stopped. The loader
must key a thread on its Notion page, so a thread that moves keeps its id; and a
title named in the manifest must never reach the publication bundle.
"""
from __future__ import annotations

import pytest

from exo.loaders import attention
from exo.notes.sources.notion import api
from exo.scripts_impl import _fetch, fetch_attention_threads as fat


@pytest.fixture(autouse=True)
def exports(tmp_path, monkeypatch):
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(_fetch.config, "EXPORTS", d)
    monkeypatch.setattr(attention.config, "EXPORTS", d)
    monkeypatch.setenv("ATTENTION_THREADS_DATABASE_ID", "4223f93ca2bc815f9053f2e9f475bd56")
    return d


def _rt(s):
    return {"type": "rich_text", "rich_text": [{"plain_text": s, "type": "text", "text": {"content": s}}]}


def _page(pid="p1", title="Housing plan", summary="Weighing rent vs buy.",
          moves="2026-11-20 started\n2026-11-24 ran the numbers", touches=4,
          last="2026-11-24T18:00:00.000Z", first="2026-11-20"):
    return {
        "id": pid, "archived": False, "last_edited_time": "2026-11-24T18:00:00.000Z",
        "properties": {
            "Thread": {"type": "title", "title": [{"plain_text": title}]},
            "Summary": _rt(summary), "Moves": _rt(moves),
            "Touches": {"type": "number", "number": touches},
            "Load": {"type": "select", "select": {"name": "Heavy"}},
            "First seen": {"type": "date", "date": {"start": first}},
            "Last touched": {"type": "date", "date": {"start": last}},
            "Authorship": {"type": "select", "select": {"name": "AI"}},
            "Status": {"type": "select", "select": {"name": "Live"}},
            "Chats": _rt("77bee321-20eb-4c39-a124-493e5a68edb5 Housing numbers"),
        },
    }


def _serve(monkeypatch, pages):
    monkeypatch.setattr(api, "_paged", lambda *a, **k: iter(pages))


def test_fetch_and_load(monkeypatch):
    _serve(monkeypatch, [_page(), _page(pid="p2", title="")])
    assert fat.run() == 0
    rows = attention.load()
    assert len(rows) == 1                     # the untitled page is dropped
    r = rows[0]
    assert r.zone == "attention_thread" and r.author == "machine"
    assert r.payload["touches"] == 4 and r.payload["load"] == "Heavy"
    assert r.payload["moves"].endswith("ran the numbers")
    assert "status" not in r.payload          # "Live" goes stale overnight; not copied
    assert r.payload["chats"].startswith("77bee321-")  # the surface links these


def test_a_thread_that_moves_keeps_its_id(monkeypatch):
    _serve(monkeypatch, [_page()])
    fat.run()
    before = attention.load()[0].id
    _serve(monkeypatch, [_page(summary="Decided to rent.", touches=5,
                               moves="2026-11-20 started\n2026-11-25 decided")])
    fat.run()
    assert attention.load()[0].id == before


def test_a_collapse_is_refused(monkeypatch):
    _serve(monkeypatch, [_page(pid=f"p{i}", title=f"t{i}") for i in range(10)])
    fat.run()
    _serve(monkeypatch, [_page(pid="p0", title="t0")])
    assert fat.run() == 1
    assert len(attention.load()) == 10


def test_no_database_id_writes_nothing(monkeypatch, exports):
    monkeypatch.delenv("ATTENTION_THREADS_DATABASE_ID")
    assert fat.run() == 1
    assert not (exports / fat.CACHE).exists()


def test_held_titles_never_reach_the_bundle():
    import duckdb

    from exo.scripts_impl.publish import _attention_select
    con = duckdb.connect()
    con.execute("CREATE TABLE t0_attention_thread (id VARCHAR, title VARCHAR)")
    con.execute("INSERT INTO t0_attention_thread VALUES ('a','Housing plan'),('b','A private one')")
    sql, params = _attention_select({"attention_threads": {"hold_titles": ["A private one"]}})
    assert [r[1] for r in con.execute(sql, params).fetchall()] == ["Housing plan"]
    sql, params = _attention_select({})          # nothing held: everything serves
    assert len(con.execute(sql, params).fetchall()) == 2
