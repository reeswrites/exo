"""`fetch-notion-writing`: the read-only Notion writing cache and its reports.

Two properties matter. Nothing is adopted by guesswork: a page adopted into the
wrong vault note hands that note's atoms to different writing, so the match is
one-to-one or nothing. And nothing is labelled AI by guesswork either: the
heuristic only ever produces a review list; `ai` comes from a decision, a
property or a marker the page itself carries.
"""
from __future__ import annotations

import json

import pytest

from exo.notes.sources.notion import api
from exo.scripts_impl import fetch_notion_writing as fw

ARCHIVE = "3c23f93ca2bc81eda111d175ac66450f"
BOOKS = "4223f93ca2bc812084e0da9d33461db2"


def _page(pid="p1", title="On caching", created="2024-03-01T19:40:00.000Z",
          folder="Drafts", edited="2026-08-20T10:00:00.000Z", db=ARCHIVE,
          author="u-person", authorship=None):
    props = {
        "Name": {"type": "title", "title": [{"plain_text": title}]},
        "Folder": {"type": "select", "select": {"name": folder} if folder else None},
        "Created": {"type": "date", "date": {"start": created} if created else None},
        "Apple Notes ID": {"type": "rich_text", "rich_text": [{"plain_text": "x-coredata://A/ICNote/p1"}]},
        "Locked": {"type": "checkbox", "checkbox": False},
        "Skip?": {"type": "checkbox", "checkbox": False},
    }
    if authorship:
        props["Authorship"] = {"type": "select", "select": {"name": authorship}}
    parent = {"type": "database_id", "database_id": db} if db else {"type": "page_id", "page_id": "home"}
    return {
        "object": "page", "id": pid, "url": f"https://notion.so/{pid}",
        "last_edited_time": edited, "created_time": "2026-08-20T09:00:00.000Z",
        "created_by": {"object": "user", "id": author},
        "last_edited_by": {"object": "user", "id": author},
        "parent": parent, "archived": False, "properties": props,
    }


def _note(ref="raw/import/2024-03-01-on-caching.md", title="On caching",
          created="2024-03-01", folder="Drafts", body="hello  world"):
    return {"origin_ref": ref, "title": title, "created": created, "folder": folder,
            "uuid": "U", "body": body}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(fw.config, "ROOT", tmp_path)
    monkeypatch.setattr(fw.config, "setting", lambda s, k, d=None: d)
    monkeypatch.setattr(api, "_same_minute", lambda edited, now=None: False)
    for v in ("EXO_NOTION_WRITING_SKIP", "EXO_NOTION_WRITING_ADOPT"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


USERS = [{"id": "u-person", "type": "person"}, {"id": "u-bot", "type": "bot"}]


def _stub(monkeypatch, pages, bodies=None):
    calls = {"search": 0, "blocks": 0}

    def paged(path, **kw):
        if path == "/users":
            return iter(USERS)
        calls["search"] += 1
        return iter(pages)

    def fetch_blocks(pid, depth=0):
        calls["blocks"] += 1
        return [{"pid": pid}]

    monkeypatch.setattr(api, "_paged", paged)
    monkeypatch.setattr(api, "fetch_blocks", fetch_blocks)
    monkeypatch.setattr(fw.blockrender, "render",
                        lambda blocks: ((bodies or {}).get(blocks[0]["pid"], "body"), []))
    return calls


def _entry(**kw):
    body = kw.pop("body", None)
    fw._USERS.update({api._norm_id(u["id"]): u["type"] for u in USERS})
    e = fw.shape(_page(**kw), {})
    if body is not None:
        e["body"] = body
    return e


# ─────────────────────────────── shape ───────────────────────────────


def test_shape_takes_the_apple_date_not_the_import_date():
    s = _entry()
    assert s["created"].startswith("2024-03-01") and s["database"] == ARCHIVE


def test_shape_falls_back_to_created_time_without_a_created_property():
    assert _entry(created=None)["created"].startswith("2026-08-20")


def test_created_by_type_comes_from_the_user_list():
    assert _entry(author="u-bot")["created_by"]["type"] == "bot"


# ─────────────────────────────── sync ───────────────────────────────


def test_first_sync_reads_every_body(root, monkeypatch):
    calls = _stub(monkeypatch, [_page("a"), _page("b", db=None)])
    blob = fw.sync()
    assert calls["blocks"] == 2 and set(blob["pages"]) == {"a", "b"}


def test_an_unchanged_page_is_not_re_read(root, monkeypatch):
    _stub(monkeypatch, [_page("a"), _page("b")])
    fw._write_json("pages.json", fw.sync())
    calls = _stub(monkeypatch, [_page("a"), _page("b", edited="2026-11-24T10:00:00.000Z")])
    blob = fw.sync()
    assert calls["blocks"] == 1 and blob["pages"]["a"]["body"] == "body"


def test_a_same_minute_edit_is_re_read_next_run(root, monkeypatch):
    _stub(monkeypatch, [_page("a")])
    monkeypatch.setattr(api, "_same_minute", lambda edited, now=None: True)
    fw._write_json("pages.json", fw.sync())
    monkeypatch.setattr(api, "_same_minute", lambda edited, now=None: False)
    calls = _stub(monkeypatch, [_page("a")])
    fw.sync()
    assert calls["blocks"] == 1


def test_rows_of_a_skipped_database_are_listed_but_never_read(root, monkeypatch):
    monkeypatch.setenv("EXO_NOTION_WRITING_SKIP", BOOKS)
    calls = _stub(monkeypatch, [_page("book", db=BOOKS), _page("essay", db=None)])
    blob = fw.sync()
    assert calls["blocks"] == 1
    assert blob["pages"]["book"]["body_skipped"] and blob["pages"]["book"]["body_chars"] == 0


def test_properties_only_reads_no_blocks(root, monkeypatch):
    calls = _stub(monkeypatch, [_page("a")])
    assert "body" not in fw.sync(bodies=False)["pages"]["a"] and calls["blocks"] == 0


def test_a_collapse_is_refused_but_a_small_delete_is_written(root, monkeypatch):
    _stub(monkeypatch, [_page(str(i)) for i in range(20)])
    fw._write_json("pages.json", fw.sync())
    _stub(monkeypatch, [_page(str(i)) for i in range(19)])
    assert len(fw.sync()["pages"]) == 19
    _stub(monkeypatch, [_page(str(i)) for i in range(5)])
    with pytest.raises(RuntimeError):
        fw.sync()


def test_an_empty_listing_is_refused(root, monkeypatch):
    _stub(monkeypatch, [])
    with pytest.raises(RuntimeError):
        fw.sync()


def test_trashed_pages_and_database_objects_are_not_pages(root, monkeypatch):
    p = _page("a")
    p["in_trash"] = True
    db = {"object": "database", "id": "d", "title": [], "parent": {}}
    _stub(monkeypatch, [p, db, _page("b")])
    assert set(fw.sync()["pages"]) == {"b"}


# ─────────────────────────────── authorship ───────────────────────────────


def test_a_marker_line_labels_the_page():
    assert fw.marker("> 🤖 Drafted with Claude, edited by me\n\ntext") == "ai"
    assert fw.marker("Edited with Claude\n\ntext") == "mixed"
    assert fw.marker("I drafted this with a friend") is None


def test_the_owners_decision_wins_over_everything():
    e = _entry(body="Drafted with Claude\n\nx", authorship="AI-drafted")
    assert fw.authorship(e, {"p1": {"author": "human", "decided": "2026-11-24"}}) == "human"


def test_an_authorship_property_beats_the_marker():
    e = _entry(body="Drafted with Claude\n\nx", authorship="Mixed")
    assert fw.authorship(e, {}) == "mixed"


def test_the_heuristic_only_ever_says_unreviewed():
    body = ("# A\n## B\n## C\n" + "word — word. " * 200
            + "\nWant me to push on any of these?")
    e = _entry(body=body)
    assert len(fw.suspicion(e)) >= 2
    assert fw.authorship(e, {}) == "unreviewed"


def test_plain_writing_is_human():
    assert fw.authorship(_entry(body="thinking about caching again. lowercase, typos."), {}) == "human"


def test_decisions_are_read_from_the_instance_root(root):
    (root / "notion-authorship.json").write_text(
        json.dumps({"p1": {"author": "mixed", "decided": "2026-11-24", "why": "x"}}))
    assert fw.decisions()["p1"]["author"] == "mixed"


# ─────────────────────────────── match ───────────────────────────────


def test_a_page_adopts_the_note_with_its_title_and_date():
    r = fw.match([_entry(body="hello world")], [_note()])
    assert r["map"] == {"p1": "raw/import/2024-03-01-on-caching.md"}
    assert r["matched_folder_agrees"] == 1 and r["matched_body_identical"] == 1


def test_a_utc_date_one_day_off_still_matches():
    assert fw.match([_entry(created="2024-03-02T01:10:00.000Z")], [_note()])["matched"] == 1


def test_title_matching_ignores_case_curly_quotes_and_spacing():
    r = fw.match([_entry(title="Why I’m  logging")], [_note(title="why i'm logging")])
    assert r["matched"] == 1


def test_a_title_the_importer_cut_short_matches_on_its_prefix_and_date():
    r = fw.match([_entry(title="Observational comedy allows you to…")],
                 [_note(title="Observational comedy allows you to pay attention")])
    assert r["matched"] == 1


def test_two_notes_with_the_same_key_are_ambiguous_not_guessed():
    notes = [_note(ref="raw/import/a.md"), _note(ref="raw/import/b.md")]
    r = fw.match([_entry()], notes)
    assert r["matched"] == 0 and r["ambiguous"] == 1


def test_two_pages_claiming_one_note_are_both_left_unmatched():
    r = fw.match([_entry(pid="p1"), _entry(pid="p2")], [_note()])
    assert r["matched"] == 0 and r["ambiguous"] == 2


def test_a_page_with_no_note_is_new_and_a_note_with_no_page_is_counted():
    r = fw.match([_entry(title="Brand new essay")], [_note()])
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


# ─────────────────────────────── run ───────────────────────────────


def test_run_writes_the_cache_and_all_three_reports(root, monkeypatch):
    vault = root / "vault"
    (vault / "raw" / "import").mkdir(parents=True)
    (vault / "raw" / "import" / "2024-03-01-on-caching.md").write_text(
        "---\ntitle: On caching\ncreated: 2024-03-01\nfolder: Drafts\n---\n\nbody\n",
        encoding="utf-8")
    monkeypatch.setattr(fw.config, "VAULT", vault)
    monkeypatch.setenv("EXO_NOTION_WRITING_ADOPT", ARCHIVE)
    _stub(monkeypatch, [_page("p1"), _page("essay", title="Hedonic Expansionism", db=None)])
    assert fw.run() == 0
    d = root / "raw" / "notion-writing"
    assert json.loads((d / "match-report.json").read_text())["map"] == {
        "p1": "raw/import/2024-03-01-on-caching.md"}
    assert set(json.loads((d / "pages.json").read_text())["pages"]) == {"p1", "essay"}
    assert json.loads((d / "authorship-review.json").read_text()) == []


def test_run_without_a_connection_keeps_the_cache_and_fails(root, monkeypatch):
    def boom(path, **kw):
        raise api.NotionError("Notion rejected the token (401).")
    monkeypatch.setattr(api, "_paged", boom)
    assert fw.run() == 1
