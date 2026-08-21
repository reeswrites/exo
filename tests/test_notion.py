"""The Notion adapter — the block renderer, the API road, and the claim that the
two roads agree.

Every byte of input here is hand-written (CONTRIBUTING). The API road is tested
against a stubbed `urlopen`, which is the only part of it worth stubbing: the
renderer and the pager are where the behaviour is, and neither needs a network
to be wrong.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from exo.notes.sources.notion import api, blocks, export


def txt(content, **ann):
    a = {"bold": False, "italic": False, "strikethrough": False,
         "underline": False, "code": False, "color": "default"}
    a.update(ann)
    return {"plain_text": content, "annotations": a, "href": ann.get("href")}


def blk(kind, rich=None, children=None, **payload):
    body = dict(payload)
    if rich is not None:
        body["rich_text"] = rich
    out = {"type": kind, "id": f"id-{kind}", kind: body}
    if children:
        out["_children"] = children
    return out


# ────────────────────────────── inline ──────────────────────────────


def test_rich_text_annotations_nest_with_code_innermost():
    """`**`x`**` renders; `` `**x**` `` renders the asterisks as literal
    characters inside the code span."""
    assert blocks.rich_text([txt("x", bold=True, code=True)]) == "**`x`**"
    assert blocks.rich_text([txt("x", italic=True, strikethrough=True)]) == "~~*x*~~"


def test_rich_text_links_wrap_the_formatting():
    got = blocks.rich_text([txt("docs", bold=True, href="https://example.com")])
    assert got == "[**docs**](https://example.com)"


def test_a_backtick_inside_a_code_span_gets_a_longer_fence():
    """An escape does not work inside a code span; a longer fence is the spec's
    own answer, and without it the span closes early and eats the rest of the
    line."""
    got = blocks.rich_text([txt("a ` b", code=True)])
    assert got == "``a ` b``"
    assert blocks.rich_text([txt("`x`", code=True)]) == "`` `x` ``"


# ────────────────────────────── blocks ──────────────────────────────


def test_headings_paragraphs_and_dividers():
    md, _ = blocks.render([
        blk("heading_1", [txt("Title")]),
        blk("paragraph", [txt("Some prose.")]),
        blk("divider"),
        blk("heading_3", [txt("Later")]),
    ])
    assert md == "# Title\n\nSome prose.\n\n---\n\n### Later"


def test_list_siblings_stay_one_list():
    """A blank line between items makes markdown render a run of one-item lists."""
    md, _ = blocks.render([
        blk("bulleted_list_item", [txt("one")]),
        blk("bulleted_list_item", [txt("two")]),
    ])
    assert md == "- one\n- two"


def test_numbering_counts_and_restarts_per_level():
    md, _ = blocks.render([
        blk("numbered_list_item", [txt("first")]),
        blk("numbered_list_item", [txt("second")], children=[
            blk("numbered_list_item", [txt("nested")]),
            blk("numbered_list_item", [txt("also nested")]),
        ]),
        blk("numbered_list_item", [txt("third")]),
    ])
    assert "1. first" in md and "2. second" in md and "3. third" in md
    assert "    1. nested" in md and "    2. also nested" in md


def test_a_paragraph_between_items_restarts_the_numbering():
    md, _ = blocks.render([
        blk("numbered_list_item", [txt("a")]),
        blk("paragraph", [txt("an aside")]),
        blk("numbered_list_item", [txt("b")]),
    ])
    assert "1. a" in md and "1. b" in md and "2." not in md


def test_todos_code_quotes_and_callouts():
    md, _ = blocks.render([
        blk("to_do", [txt("done")], checked=True),
        blk("to_do", [txt("not done")], checked=False),
    ])
    assert md == "- [x] done\n- [ ] not done"

    md, _ = blocks.render([blk("code", [txt("print(1)")], language="python")])
    assert md == "```python\nprint(1)\n```"

    md, _ = blocks.render([blk("callout", [txt("watch out")], icon={"emoji": "!"})])
    assert md == "> ! watch out"


def test_media_and_links_keep_their_url():
    md, _ = blocks.render([
        blk("image", caption=[txt("a diagram")], file={"url": "https://x/i.png"}),
        blk("bookmark", url="https://example.com", caption=[]),
    ])
    assert "![a diagram](https://x/i.png)" in md
    assert "[https://example.com](https://example.com)" in md


def test_columns_flatten_and_keep_their_text():
    """Markdown has no opinion about a two-column layout. Keeping the words and
    losing the geometry is the only honest option."""
    md, _ = blocks.render([
        blk("column_list", children=[
            blk("column", children=[blk("paragraph", [txt("left")])]),
            blk("column", children=[blk("paragraph", [txt("right")])]),
        ]),
    ])
    assert "left" in md and "right" in md
    assert "column" not in md


def test_a_subpage_is_not_this_pages_text():
    """It comes back from search in its own right and lands as its own note."""
    md, _ = blocks.render([
        blk("paragraph", [txt("mine")]),
        blk("child_page", title="Somewhere else"),
    ])
    assert md == "mine"


def test_an_unrenderable_block_is_counted_not_silently_dropped():
    """A silent skip is how a paragraph disappears between two that still read
    fine, which is the hardest kind of loss to notice."""
    md, skipped = blocks.render([
        blk("paragraph", [txt("kept")]),
        blk("table_of_contents"),
        blk("unsupported"),
    ])
    assert md == "kept"
    assert sorted(skipped) == ["table_of_contents", "unsupported"]


# ────────────────────────────── page shape ──────────────────────────────


def test_a_renamed_title_property_is_still_the_title():
    """A database row's title property can be called anything, so it is found by
    type rather than by name."""
    page = {"object": "page", "properties": {
        "Book": {"type": "title", "title": [txt("Piranesi")]},
        "Rating": {"type": "number", "number": 5},
    }}
    assert api.title_of(page) == "Piranesi"


def test_properties_render_as_the_content_of_a_database_row():
    """For a row the properties ARE the writing — a body-only import lands it
    empty — and the order is stable so an unchanged row re-imports identically."""
    page = {"object": "page", "properties": {
        "Book": {"type": "title", "title": [txt("Piranesi")]},
        "Rating": {"type": "number", "number": 5},
        "Tags": {"type": "multi_select", "multi_select": [{"name": "fiction"}, {"name": "reread"}]},
        "Read": {"type": "checkbox", "checkbox": True},
        "When": {"type": "date", "date": {"start": "2026-02-03", "end": None}},
        "Empty": {"type": "rich_text", "rich_text": []},
        "Edited": {"type": "last_edited_time", "last_edited_time": "2026-08-01T00:00:00.000Z"},
    }}
    assert api.properties_block(page) == (
        "Rating: 5\nRead: Yes\nTags: fiction, reread\nWhen: 2026-02-03")


def test_folder_is_the_ancestry_and_stops_where_the_integration_cannot_see():
    index = {
        "cc": {"title": "On caching", "parent": "bb"},
        "bb": {"title": "Books", "parent": "aa"},
        "aa": {"title": "Reading", "parent": "zz"},   # zz was never connected
    }
    assert api._folder_of("cc", index) == "Reading/Books"
    assert api._folder_of("aa", index) == ""


def test_a_parent_cycle_terminates():
    index = {"a": {"title": "A", "parent": "b"}, "b": {"title": "B", "parent": "a"}}
    assert api._folder_of("a", index) in ("B/A", "A/B")   # bounded, whatever the order


def test_both_roads_produce_the_same_id():
    """The claim the one-adapter-two-roads design rests on: a page read through
    the API and the same page read out of an export filename land as ONE note."""
    dashed = "0123abcd-4567-89ef-0123-456789abcdef"
    _title, from_export = export._strip_id(f"Reading {dashed.replace('-', '')}")
    assert api._norm_id(dashed) == from_export


# ────────────────────────────── the API road ──────────────────────────────


class _Stub:
    """Serves canned responses in order, recording the requests it was given."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, json.loads(req.data) if req.data else None))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


class _Resp(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setattr(api, "_MIN_GAP", 0.0)
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)


def _page(pid, title, edited, parent=None, **extra):
    page = {
        "object": "page", "id": pid,
        "created_time": "2026-01-02T10:00:00.000Z",
        "last_edited_time": edited,
        "parent": {"type": "page_id", "page_id": parent} if parent
                  else {"type": "workspace", "workspace": True},
        "properties": {"Name": {"type": "title", "title": [txt(title)]}},
    }
    page.update(extra)
    return page


def test_search_pagination_is_followed(monkeypatch):
    stub = _Stub([
        {"results": [_page("aaa", "One", "2026-08-01T00:00:00.000Z")],
         "has_more": True, "next_cursor": "CURSOR"},
        {"results": [_page("bbb", "Two", "2026-08-01T00:00:00.000Z")],
         "has_more": False, "next_cursor": None},
        {"results": [], "has_more": False},   # blocks for aaa
        {"results": [], "has_more": False},   # blocks for bbb
    ])
    monkeypatch.setattr(api, "_urlopen", stub)
    got = api.read({})
    assert {n.title for n in got} == {"One", "Two"}
    assert stub.calls[1][1]["start_cursor"] == "CURSOR"


def test_an_unchanged_page_is_never_opened(monkeypatch):
    """The whole reason the API road is the default one: search costs a request
    per hundred pages, blocks cost a request per hundred BLOCKS."""
    edited = "2026-08-01T00:00:00.000Z"
    stub = _Stub([{"results": [_page("aaa", "One", edited)], "has_more": False}])
    monkeypatch.setattr(api, "_urlopen", stub)

    got = api.read({"aaa": {"edited": edited, "title": "One", "created": "2026-01-02",
                            "folder": "Reading", "_body": "what is on disk"}})
    assert len(stub.calls) == 1                     # search only; no /blocks/ call
    assert len(got) == 1                            # handed back, not omitted
    assert got[0].body == "what is on disk"
    assert got[0].folder == "Reading"


def test_an_edited_page_is_re_read(monkeypatch):
    stub = _Stub([
        {"results": [_page("aaa", "One", "2026-08-20T00:00:00.000Z")], "has_more": False},
        {"results": [blk("paragraph", [txt("the new text")])], "has_more": False},
    ])
    monkeypatch.setattr(api, "_urlopen", stub)
    got = api.read({"aaa": {"edited": "2026-08-01T00:00:00.000Z", "_body": "stale"}})
    assert got[0].body == "the new text"
    assert got[0].extra["edited"] == "2026-08-20T00:00:00.000Z"


def test_a_database_object_is_not_a_note(monkeypatch):
    """Its rows come back from search as pages in their own right. Importing the
    container too lands an empty note named after it beside every row."""
    stub = _Stub([
        {"results": [
            {"object": "database", "id": "ddd", "title": [txt("Reading log")],
             "parent": {"type": "workspace"}},
            _page("rrr", "Piranesi", "2026-08-01T00:00:00.000Z", parent="ddd"),
        ], "has_more": False},
        {"results": [], "has_more": False},
    ])
    monkeypatch.setattr(api, "_urlopen", stub)
    got = api.read({})
    assert [n.title for n in got] == ["Piranesi"]
    # the database still names the folder, even though it is not itself a note
    assert got[0].folder == "Reading log"


def test_a_trashed_page_does_not_land(monkeypatch):
    stub = _Stub([
        {"results": [_page("aaa", "Gone", "2026-08-01T00:00:00.000Z", in_trash=True)],
         "has_more": False},
        {"results": [], "has_more": False},
    ])
    monkeypatch.setattr(api, "_urlopen", stub)
    assert api.read({}) == []


def test_a_rate_limit_waits_the_time_notion_asked_for(monkeypatch):
    waited = []
    monkeypatch.setattr(api.time, "sleep", lambda s: waited.append(s))
    stub = _Stub([
        urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "7"}, None),
        {"results": [], "has_more": False},
    ])
    monkeypatch.setattr(api, "_urlopen", stub)
    api.read({})
    assert 7 in waited


def test_a_missing_token_says_what_to_do(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(api.NotionError, match="my-integrations"):
        api.read({})


def test_a_404_blames_the_connection_not_the_path(monkeypatch):
    """It is almost never a wrong id — it is a page the integration was never
    connected to, and saying so is the difference between a fix and a hunt."""
    err = urllib.error.HTTPError("u", 404, "nope", {}, io.BytesIO(b'{"message":"Not found"}'))
    monkeypatch.setattr(api, "_urlopen", _Stub([err]))
    with pytest.raises(api.NotionError, match="not connected"):
        api.read({})
