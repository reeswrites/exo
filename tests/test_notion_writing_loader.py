"""loaders/notion_writing.overlay — Notion pages folded into t1_notes."""
from __future__ import annotations

import pytest

from exo.loaders import notion_writing as nw
from exo.provenance import Row

ARCHIVE = "a" * 32
BOT = {"type": "unknown", "id": "bot"}
ME = {"type": "unknown", "id": "me"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(nw.config, "ROOT", tmp_path)
    monkeypatch.setattr(nw.config, "VAULT", tmp_path / "vault")
    monkeypatch.setattr(nw.config, "setting", lambda s, k, d=None: d)
    monkeypatch.setenv("EXO_NOTION_WRITING_ADOPT", ARCHIVE)
    monkeypatch.setenv("EXO_NOTION_WRITING_EXCLUDE", "Private")
    monkeypatch.setenv("EXO_NOTION_WRITING_MIXED", "Research Log")
    monkeypatch.delenv("EXO_NOTION_WRITING_SKIP", raising=False)
    return tmp_path


def _vault_row(ref, title="On caching", body="hello world", uuid="U", created="2024-03-01",
               folder="Notes", modified="", version=1, root=None):
    if root is not None:
        f = root / "vault" / ref
        f.parent.mkdir(parents=True, exist_ok=True)
        extra = f"modified: {modified}\nversion: {version}\n" if modified else ""
        f.write_text(f"---\ntitle: {title}\nuuid: {uuid}\n{extra}---\n\n{body}\n")
    return Row(tier="t1", zone="note", source="second-brain", author="human",
               created=created, origin_ref=ref,
               payload={"title": title, "note_type": "raw", "voice": "", "facet": "",
                        "folder": folder, "uuid": uuid, "body": body})


def _page(pid, title="On caching", body="hello world", created="2024-03-01T10:00:00.000Z",
          folder="", path="Home", database="", created_by=BOT, last_edited_by=BOT, **kw):
    return {"page_id": pid, "title": title, "body": body, "body_chars": len(body),
            "created": created, "folder": folder, "path": path, "database": database,
            "created_by": created_by, "last_edited_by": last_edited_by,
            "authorship_prop": "", "apple_notes_id": "x" if database else "", **kw}


def _cache(*pages):
    return {"pages": {p["page_id"]: p for p in pages}}


LONG = "a considered paragraph of his own writing. " * 10


def test_no_cache_changes_nothing():
    rows = [_vault_row("raw/import/a.md")]
    assert nw.overlay(rows, cache=None) is rows or nw.overlay(rows) == rows


def test_an_adopted_note_keeps_its_id_source_ref_and_vault_folder():
    row = _vault_row("raw/import/a.md", folder="Notes")
    [out] = nw.overlay([row], _cache(_page("p1", database=ARCHIVE, folder="Drafts")))
    assert (out.id, out.source, out.origin_ref) == (row.id, "second-brain", "raw/import/a.md")
    assert out.payload["folder"] == "Notes"
    assert out.payload["authorship"] == "human"


def test_an_unadopted_vault_note_keeps_its_id_and_gets_no_verdict():
    row = _vault_row("raw/import/a.md")
    [out] = nw.overlay([row], _cache())
    assert out.id == row.id and out.payload["authorship"] == ""


def test_older_edit_versions_leave_the_index(env):
    a = _vault_row("raw/import/a.md", modified="2026-08-17T23:00", root=env)
    b = _vault_row("raw/import/a-v2.md", modified="2026-08-18T00:26", version=2, root=env)
    out = nw.overlay([a, b], _cache(_page("p1", database=ARCHIVE)))
    assert [r.origin_ref for r in out] == ["raw/import/a-v2.md"]
    assert out[0].payload["authorship"] == "human"


def test_a_notion_edit_replaces_the_body_but_a_spelling_difference_does_not():
    row = _vault_row("raw/import/a.md", body="I _really_ mean it\n1. one")
    same = _page("p1", database=ARCHIVE, body="I *really* mean it\n- one", last_edited_by=ME)
    [out] = nw.overlay([row], _cache(same))
    assert out.payload["body"] == row.payload["body"] and out.id == row.id

    edited = _page("p1", database=ARCHIVE, body="I mean it, and more", last_edited_by=ME)
    [out] = nw.overlay([row], _cache(edited))
    assert out.payload["body"] == "I mean it, and more"

    by_importer = _page("p1", database=ARCHIVE, body="I mean it, and more")
    [out] = nw.overlay([row], _cache(by_importer))
    assert out.payload["body"] == row.payload["body"]


def test_new_pages_become_their_own_rows():
    out = nw.overlay([], _cache(
        _page("p1", title="Essay", body=LONG, path="Home/Writing", created_by=ME),
        _page("p2", title="Travel Plans", body=LONG, database=ARCHIVE, folder="Notes",
              created="2022-09-19T16:39:00.000Z")))
    by = {r.payload["uuid"]: r for r in out}
    assert by["p1"].source == "notion-writing"
    assert by["p1"].origin_ref == "raw/notion-writing/p1"
    assert by["p1"].payload["folder"] == ""          # unfiled -> held
    assert by["p2"].payload["folder"] == "Notes"     # archive keeps its folder
    assert by["p2"].created == "2022-09-19"


def test_private_short_and_ambiguous_pages_are_not_indexed():
    rows = [_vault_row("raw/import/a.md", uuid="A"), _vault_row("raw/import/b.md", uuid="B")]
    out = nw.overlay(rows, _cache(
        _page("p1", title="Shopping", body=LONG, database=ARCHIVE, folder="Private/Shopping"),
        _page("p2", title="People", body=LONG, path="Home/Private/People"),
        _page("p3", title="Stub", body="short"),
        _page("p4", database=ARCHIVE, body=LONG)))       # ties a.md and b.md
    assert {r.origin_ref for r in out} == {"raw/import/a.md", "raw/import/b.md"}


def test_ai_pages_are_machine_and_not_grounds_mixed_stay_human():
    out = nw.overlay([], _cache(
        _page("p1", title="Draft", body="🤖 Drafted with Claude\n\n" + LONG, path="Home"),
        _page("p2", title="Week 12", body=LONG, path="Home/Research Log")))
    by = {r.payload["uuid"]: r for r in out}
    assert (by["p1"].author, by["p1"].grounds, by["p1"].payload["authorship"]) == ("machine", False, "ai")
    assert (by["p2"].author, by["p2"].grounds, by["p2"].payload["authorship"]) == ("human", True, "mixed")


def test_authorship_is_left_out_of_the_id():
    row = _vault_row("raw/import/a.md")
    payload = dict(row.payload, authorship="mixed")
    assert nw.note_id("second-brain", payload) == row.id


def test_a_chat_digest_carries_its_chat_link_without_changing_any_note_id():
    url = "https://claude.ai/chat/77bee321-20eb-4c39-a124-493e5a68edb5"
    [plain] = nw.overlay([], _cache(_page("p1", title="Digest", body=LONG, created_by=ME)))
    [linked] = nw.overlay([], _cache(_page("p1", title="Digest", body=LONG, created_by=ME,
                                           chat_url=url)))
    assert linked.payload["link"] == url
    assert plain.payload["link"] == ""
    # A pointer about the note, not its content: the id (and first_seen with it)
    # must be the one the note had before links existed.
    assert linked.id == plain.id
    before = {k: v for k, v in plain.payload.items() if k != "link"}
    assert nw.note_id(plain.source, before) == plain.id


def test_every_note_carries_its_notion_page_without_changing_any_id():
    # A Notion-native note: its own page.
    [new] = nw.overlay([], _cache(_page("p9", title="Essay", body=LONG, created_by=ME)))
    assert new.payload["notion_id"] == "p9"
    before = {k: v for k, v in new.payload.items() if k != "notion_id"}
    assert nw.note_id(new.source, before) == new.id
    # A vault note adopted onto a Notion page: that page.
    row = _vault_row("raw/import/a.md", folder="Notes")
    [out] = nw.overlay([row], _cache(_page("p1", database=ARCHIVE, folder="Drafts")))
    assert out.payload["notion_id"] == "p1" and out.id == row.id
    # A vault note Notion never received: none.
    [lone] = nw.overlay([_vault_row("raw/import/b.md")], _cache())
    assert lone.payload["notion_id"] == ""
