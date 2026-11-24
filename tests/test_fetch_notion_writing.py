"""`fetch-notion-writing`: the read-only Notion writing cache and its match report.

The property that matters is that nothing gets adopted by guesswork. A Notion
page adopted into the wrong vault note hands that note's atoms, id and history to
different writing, so the match is one-to-one or nothing.
"""
from __future__ import annotations

import json

import pytest

from exo.notes.sources.notion import api
from exo.scripts_impl import fetch_notion_writing as fw


def _page(pid="p1", title="On caching", created="2024-03-01T19:40:00.000Z",
          folder="Drafts", edited="2026-08-20T10:00:00.000Z", apple="x-coredata://A/ICNote/p1"):
    return {
        "id": pid, "url": f"https://notion.so/{pid}", "last_edited_time": edited,
        "created_time": "2026-08-20T09:00:00.000Z", "archived": False,
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": title}]},
            "Folder": {"type": "select", "select": {"name": folder} if folder else None},
            "Created": {"type": "date", "date": {"start": created}},
            "Modified": {"type": "date", "date": None},
            "Apple Notes ID": {"type": "rich_text", "rich_text": [{"plain_text": apple}]},
            "Locked": {"type": "checkbox", "checkbox": False},
            "Skip?": {"type": "checkbox", "checkbox": False},
            "Published?": {"type": "checkbox", "checkbox": False},
        },
    }


def _note(ref="raw/import/2024-03-01-on-caching.md", title="On caching",
          created="2024-03-01", folder="Drafts", body="hello  world"):
    return {"origin_ref": ref, "title": title, "created": created, "folder": folder,
            "uuid": "U", "body": body}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(fw.config, "ROOT", tmp_path)
    monkeypatch.setattr(api, "_same_minute", lambda edited, now=None: False)
    return tmp_path


def _stub(monkeypatch, pages, bodies=None):
    calls = {"query": 0, "blocks": 0}

    def paged(path, **kw):
        calls["query"] += 1
        return iter(pages)

    def fetch_blocks(pid, depth=0):
        calls["blocks"] += 1
        return [{"pid": pid}]

    monkeypatch.setattr(api, "_paged", paged)
    monkeypatch.setattr(api, "fetch_blocks", fetch_blocks)
    monkeypatch.setattr(fw.blockrender, "render",
                        lambda blocks: ((bodies or {}).get(blocks[0]["pid"], "body"), []))
    return calls


# ─────────────────────────────── shape ───────────────────────────────


def test_shape_takes_the_apple_date_not_the_import_date():
    s = fw.shape(_page())
    assert s["created"].startswith("2024-03-01")
    assert s["folder"] == "Drafts" and s["apple_notes_id"].startswith("x-coredata://")


def test_shape_falls_back_to_created_time_for_a_page_with_no_created_property():
    p = _page()
    del p["properties"]["Created"]
    assert fw.shape(p)["created"].startswith("2026-08-20")


# ─────────────────────────────── sync ───────────────────────────────


def test_first_sync_reads_every_body(root, monkeypatch):
    calls = _stub(monkeypatch, [_page("a"), _page("b")])
    blob = fw.sync("db")
    assert calls["blocks"] == 2 and set(blob["pages"]) == {"a", "b"}


def test_an_unchanged_page_is_not_re_read(root, monkeypatch):
    _stub(monkeypatch, [_page("a"), _page("b")])
    fw.write("db", fw.sync("db"))
    calls = _stub(monkeypatch, [_page("a"), _page("b", edited="2026-11-24T10:00:00.000Z")])
    blob = fw.sync("db")
    assert calls["blocks"] == 1                         # only b moved
    assert blob["pages"]["a"]["body"] == "body"         # a's body carried


def test_a_same_minute_edit_is_re_read_next_run(root, monkeypatch):
    _stub(monkeypatch, [_page("a")])
    monkeypatch.setattr(api, "_same_minute", lambda edited, now=None: True)
    fw.write("db", fw.sync("db"))
    monkeypatch.setattr(api, "_same_minute", lambda edited, now=None: False)
    calls = _stub(monkeypatch, [_page("a")])
    fw.sync("db")
    assert calls["blocks"] == 1


def test_properties_only_reads_no_blocks(root, monkeypatch):
    calls = _stub(monkeypatch, [_page("a")])
    blob = fw.sync("db", bodies=False)
    assert calls["blocks"] == 0 and "body" not in blob["pages"]["a"]


def test_a_collapse_is_refused_but_a_small_delete_is_written(root, monkeypatch):
    _stub(monkeypatch, [_page(str(i)) for i in range(20)])
    fw.write("db", fw.sync("db"))
    _stub(monkeypatch, [_page(str(i)) for i in range(19)])
    assert len(fw.sync("db")["pages"]) == 19
    _stub(monkeypatch, [_page(str(i)) for i in range(5)])
    with pytest.raises(RuntimeError):
        fw.sync("db")


def test_an_empty_query_is_refused(root, monkeypatch):
    _stub(monkeypatch, [])
    with pytest.raises(RuntimeError):
        fw.sync("db")


def test_trashed_pages_are_dropped(root, monkeypatch):
    p = _page("a")
    p["in_trash"] = True
    _stub(monkeypatch, [p, _page("b")])
    assert set(fw.sync("db")["pages"]) == {"b"}


def test_the_database_id_is_queried_dashless(root, monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_paged", lambda path, **kw: seen.append(path) or iter([_page()]))
    monkeypatch.setattr(api, "fetch_blocks", lambda pid, depth=0: [])
    monkeypatch.setattr(fw.blockrender, "render", lambda b: ("", []))
    fw.sync("3c23f93c-a2bc-81ed-a111-d175ac66450f")
    assert seen == ["/databases/3c23f93ca2bc81eda111d175ac66450f/query"]


# ─────────────────────────────── match ───────────────────────────────


def _pg(**kw):
    s = fw.shape(_page(**{k: v for k, v in kw.items() if k != "body"}))
    if "body" in kw:
        s["body"] = kw["body"]
    return s


def test_a_page_adopts_the_note_with_its_title_and_date():
    r = fw.match([_pg(body="hello world")], [_note()])
    assert r["map"] == {"p1": "raw/import/2024-03-01-on-caching.md"}
    assert r["matched_folder_agrees"] == 1 and r["matched_body_identical"] == 1


def test_a_utc_date_one_day_off_still_matches():
    r = fw.match([_pg(created="2024-03-02T01:10:00.000Z")], [_note()])
    assert r["matched"] == 1


def test_title_matching_ignores_case_curly_quotes_and_spacing():
    r = fw.match([_pg(title="Why I’m  logging")], [_note(title="why i'm logging")])
    assert r["matched"] == 1


def test_a_title_the_importer_cut_short_matches_on_its_prefix_and_date():
    r = fw.match([_pg(title="Observational comedy allows you to\u2026")],
                 [_note(title="Observational comedy allows you to pay attention")])
    assert r["matched"] == 1


def test_two_notes_with_the_same_key_are_ambiguous_not_guessed():
    notes = [_note(ref="raw/import/a.md"), _note(ref="raw/import/b.md")]
    r = fw.match([_pg()], notes)
    assert r["matched"] == 0 and r["ambiguous"] == 1


def test_two_pages_claiming_one_note_are_both_left_unmatched():
    r = fw.match([_pg(pid="p1"), _pg(pid="p2")], [_note()])
    assert r["matched"] == 0 and r["ambiguous"] == 2


def test_a_page_with_no_note_is_new_and_a_note_with_no_page_is_counted():
    r = fw.match([_pg(title="Brand new essay")], [_note()])
    assert r["new"] == 1 and r["vault_unmatched"] == 1


def test_vault_notes_reads_the_import_tree(tmp_path):
    d = tmp_path / "raw" / "import"
    d.mkdir(parents=True)
    (d / "2024-03-01-x.md").write_text(
        "---\ntitle: X\ncreated: 2024-03-01\nfolder: Drafts\nuuid: U\n---\n\nbody\n",
        encoding="utf-8")
    [n] = fw.vault_notes(tmp_path)
    assert n == {"origin_ref": "raw/import/2024-03-01-x.md", "title": "X",
                 "created": "2024-03-01", "folder": "Drafts", "uuid": "U", "body": "body\n"}


def test_run_with_nothing_configured_says_how(root, monkeypatch, capsys):
    monkeypatch.delenv("EXO_NOTION_WRITING_DATABASES", raising=False)
    monkeypatch.setattr(fw.config, "setting", lambda s, k, d=None: d)
    assert fw.run() == 1
    assert "notion_writing" in capsys.readouterr().out


def test_run_writes_the_cache_and_the_report(root, monkeypatch):
    vault = root / "vault"
    (vault / "raw" / "import").mkdir(parents=True)
    (vault / "raw" / "import" / "2024-03-01-on-caching.md").write_text(
        "---\ntitle: On caching\ncreated: 2024-03-01\nfolder: Drafts\n---\n\nbody\n",
        encoding="utf-8")
    monkeypatch.setattr(fw.config, "VAULT", vault)
    _stub(monkeypatch, [_page("p1")])
    assert fw.run(database=["db"]) == 0
    rep = json.loads((root / "raw" / "notion-writing" / "match-report.json").read_text())
    assert rep["map"] == {"p1": "raw/import/2024-03-01-on-caching.md"}
