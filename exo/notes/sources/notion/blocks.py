"""Notion's block tree -> markdown. No network, no credentials, pure function.

This is the part of the API road worth testing, and the reason it is a module of
its own: everything else is pagination and a bearer token, but a workspace's
prose only survives the trip if this is right. Given the block JSON the API
returns, it renders the markdown a note is made of.

## Why the export could not just do this for us

Notion's own Markdown export renders the same tree, and it renders it *worse* in
the ways that matter to a record: page titles are truncated and the id is glued
to the filename, database rows lose their properties into a sibling CSV, and
`created`/`edited` arrive as localised prose ("August 1, 2026") rather than
timestamps. The API hands over `created_time` and `last_edited_time` as ISO
strings and the properties as typed values. Rendering it here is not extra work
— it is the same work, done from better input.

## What is deliberately lossy

A block type nobody has a markdown spelling for (`unsupported`, an embed of a
third-party app, a synced block pointing somewhere unshared) renders as nothing
and is COUNTED. A silent skip in a note importer is how a paragraph disappears
between two that still read fine, which is the hardest kind of loss to notice.

Columns and synced blocks flatten into their children: a two-column layout is a
visual arrangement, and markdown has no opinion about it, so keeping the text and
losing the geometry is the only honest option.
"""
from __future__ import annotations

# Prefixes for the block types that are a line of text with a marker in front.
_PREFIX = {
    "paragraph": "",
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "quote": "> ",
    "toggle": "- ",
}

# Types whose children ARE the content — the block itself renders nothing.
_TRANSPARENT = {"column_list", "column", "synced_block", "table"}

# Types that are a page of their own. Their text belongs to that page, not this
# one; search returns them separately and they land as their own notes.
_ELSEWHERE = {"child_page", "child_database"}


def rich_text(spans: list) -> str:
    """Notion's rich_text array -> inline markdown.

    Order is not cosmetic: `code` has to be the innermost wrapper, because
    ``**`x`**`` renders and ``` `**x**` ``` renders the asterisks as literal
    characters inside the code span.
    """
    out: list[str] = []
    for span in spans or []:
        text = span.get("plain_text", "")
        if not text:
            continue
        ann = span.get("annotations") or {}
        if ann.get("code"):
            # A backtick inside the text would close the span early; the CommonMark
            # answer is a longer fence, not an escape.
            fence = "`" * (max((len(r) for r in _runs(text, "`")), default=0) + 1)
            pad = " " if text.startswith("`") or text.endswith("`") else ""
            text = f"{fence}{pad}{text}{pad}{fence}"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        href = span.get("href")
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _runs(text: str, ch: str) -> list[str]:
    runs, cur = [], ""
    for c in text:
        if c == ch:
            cur += c
        elif cur:
            runs.append(cur)
            cur = ""
    if cur:
        runs.append(cur)
    return runs


def _file_url(payload: dict) -> str:
    """A file block's URL. Notion serves uploaded files from a SIGNED url that
    expires in an hour, so a link to one is a link to a 403 by tomorrow. Both are
    recorded anyway: an expired link still says what was there, and an external
    one still works."""
    f = payload.get("file") or payload.get("external") or payload
    return f.get("url", "") if isinstance(f, dict) else ""


def _render_one(block: dict, depth: int, counters: dict, skipped: list) -> list[str]:
    kind = block.get("type", "")
    payload = block.get(kind) or {}
    indent = "    " * depth
    text = rich_text(payload.get("rich_text") or [])

    if kind in _ELSEWHERE:
        return []
    if kind in _TRANSPARENT:
        return []
    if kind == "divider":
        return [f"{indent}---"]
    if kind == "code":
        lang = payload.get("language") or ""
        body = rich_text(payload.get("rich_text") or [])
        lines = [f"{indent}```{lang}"]
        lines += [indent + ln for ln in body.split("\n")]
        lines.append(f"{indent}```")
        return lines
    if kind == "to_do":
        mark = "x" if payload.get("checked") else " "
        return [f"{indent}- [{mark}] {text}"]
    if kind == "numbered_list_item":
        # Numbering restarts per nesting level, and a sibling paragraph between
        # two items does NOT restart it — which is the whole reason a counter is
        # threaded through here rather than every item being written as "1.".
        counters[depth] = counters.get(depth, 0) + 1
        for deeper in [d for d in counters if d > depth]:
            del counters[deeper]
        return [f"{indent}{counters[depth]}. {text}"]
    if kind == "callout":
        icon = ((payload.get("icon") or {}).get("emoji") or "").strip()
        return [f"{indent}> {icon + ' ' if icon else ''}{text}"]
    if kind == "equation":
        return [f"{indent}$${payload.get('expression', '')}$$"]
    if kind == "table_row":
        cells = [rich_text(c) for c in payload.get("cells") or []]
        return [f"{indent}| " + " | ".join(cells) + " |"]
    if kind in ("image", "video", "audio", "file", "pdf"):
        url = _file_url(payload)
        caption = rich_text(payload.get("caption") or []) or kind
        return [f"{indent}![{caption}]({url})" if kind == "image"
                else f"{indent}[{caption}]({url})"]
    if kind in ("bookmark", "embed", "link_preview"):
        url = payload.get("url", "")
        caption = rich_text(payload.get("caption") or []) or url
        return [f"{indent}[{caption}]({url})"]
    if kind in _PREFIX:
        if not text:
            return []
        return [f"{indent}{_PREFIX[kind]}{text}"]

    skipped.append(kind)
    return []


def render(blocks: list, depth: int = 0, counters: dict | None = None,
           skipped: list | None = None) -> tuple[str, list[str]]:
    """(markdown, skipped block types). `blocks` carry their children inline as
    `_children`, put there by the fetcher — this module never makes a request.

    A blank line between top-level blocks and none between list siblings, which
    is what keeps a list a list rather than a run of one-item lists.
    """
    counters = {} if counters is None else counters
    skipped = [] if skipped is None else skipped
    lines: list[str] = []
    prev_kind = ""

    for block in blocks or []:
        kind = block.get("type", "")
        own = _render_one(block, depth, counters, skipped)
        if kind not in ("numbered_list_item", "bulleted_list_item", "to_do", "table_row"):
            counters.clear()
        listish = kind in ("numbered_list_item", "bulleted_list_item", "to_do", "table_row")
        if lines and own and not (listish and prev_kind == kind):
            lines.append("")
        lines += own
        if own:
            prev_kind = kind

        children = block.get("_children") or []
        if children and kind not in _ELSEWHERE:
            # A transparent block does not indent its children — there is no
            # marker for them to hang under.
            deeper = depth if (kind in _TRANSPARENT or not own) else depth + 1
            sub, _ = render(children, deeper, counters if deeper == depth else {}, skipped)
            if sub:
                if lines and not listish:
                    lines.append("")
                lines += sub.split("\n")
                prev_kind = ""

    return "\n".join(lines).strip("\n"), skipped
