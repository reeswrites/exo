"""The Notion API road — what `exo ingest-notes notion` does with no `--from`.

The export road (`export.py`) needs a human to open Notion, click Export, wait
for an email, and move a zip out of Downloads. There is no endpoint that triggers
that, so an export can never be a step in a nightly — and notes are the freshest,
most-edited thing in the record. Making them the one source that runs only when
somebody remembers is backwards. This road is the default for that reason.

Nothing here is new ground for the engine: Trakt ships with a full OAuth refresh,
Raindrop with a bearer token, collections with a Google service account. Notion
is a service anyone can hold an account with, exactly like those.

## Setup, once

    1. notion.so/my-integrations -> New internal integration -> copy the secret
    2. In Notion, open the top-level pages you want Exo to hold and
       ... -> Connections -> add the integration. Subpages are included.
    3. echo 'NOTION_TOKEN=ntn_...' >> ~/.config/exo/secrets/env   (mode 600)

Step 2 is the whole privacy boundary on this side, and it is a good one: what
you did not connect, this cannot see. It is the same act as `EXO_PROJECTS_DENY`
for repos — exclusion by never being readable, rather than by being filed.

## Incremental by default

`POST /v1/search` returns every connected page with its `last_edited_time`, and
that costs one request per hundred pages. Fetching a page's *blocks* is what
costs — one request per hundred blocks, per nesting level, at Notion's ~3
requests/second. So a page whose `last_edited_time` matches what is already
landed is never opened at all, and a nightly over a settled workspace spends a
few seconds instead of ten minutes.

The one hole, stated because it is invisible otherwise: `last_edited_time` has
minute granularity. An edit made in the same minute as the fetch, after the
fetch read the page, keeps a timestamp this will later read as unchanged. It
needs two edits in one minute straddling the request. `--full` re-reads
everything and is the answer when it matters.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from ... import SourceError, SourceNote
from . import blocks as blockrender

API = "https://api.notion.com/v1"
# Pinned, never "latest". Notion's versioning is the contract: an unpinned client
# is one that changes shape on somebody else's deploy schedule.
VERSION = "2022-06-28"

PAGE_SIZE = 100
# Notion documents an average of three requests per second per integration. The
# gap is deliberately a little wider than 1/3s — the limit is enforced over a
# window, and being right at it means every burst pays a 429 and its Retry-After.
_MIN_GAP = 0.36
_last_call = 0.0

# Properties that are provenance rather than content. `created_time` and
# `last_edited_time` already ride in the frontmatter; rendering them into the
# body too would put a timestamp in front of every database row for the atomizer
# to cut into a span.
_ENVELOPE_PROPS = {"created_time", "last_edited_time", "created_by",
                   "last_edited_by", "rollup", "relation"}


class NotionError(SourceError):
    """Kept as its own name so a caller can tell Notion's failures apart, and as
    a SourceError so the CLI prints it rather than a traceback."""


def _token() -> str:
    token = (os.environ.get("NOTION_TOKEN") or "").strip()
    if not token:
        raise NotionError(
            "NOTION_TOKEN is not set. Create an internal integration at "
            "notion.so/my-integrations, connect it to the pages you want held, "
            "and put the secret in ~/.config/exo/secrets/env as NOTION_TOKEN=...")
    return token


def _request(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    """One API call, rate-limited and retried on 429.

    urllib rather than a dependency, like every other puller here: this runs on
    a laptop whose Python is whatever the laptop has.
    """
    global _last_call
    gap = _MIN_GAP - (time.monotonic() - _last_call)
    if gap > 0:
        time.sleep(gap)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
            "User-Agent": "exo/1.0",
        },
    )
    for attempt in range(4):
        try:
            with _urlopen(req, timeout=60) as resp:
                _last_call = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _last_call = time.monotonic()
            if exc.code == 429 and attempt < 3:
                # Notion says how long to wait. Guessing instead is how a
                # backoff turns into a second rate-limit breach.
                wait = float(exc.headers.get("Retry-After") or 2 ** attempt)
                time.sleep(min(wait, 30))
                continue
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("message", "")
            except Exception:
                pass
            if exc.code == 401:
                raise NotionError(f"Notion rejected the token (401). {detail}") from exc
            if exc.code == 404:
                raise NotionError(
                    f"Notion returned 404 for {path}. The integration is probably not "
                    f"connected to that page — connect it in Notion. {detail}") from exc
            raise NotionError(f"Notion {exc.code} on {path}: {detail}") from exc
    raise NotionError(f"Notion kept rate-limiting {path}")


# Named so a test can replace it without a live token or a live network.
_urlopen = urllib.request.urlopen


def _paged(path: str, *, method: str = "GET", body: dict | None = None):
    """Every result across Notion's cursor pagination.

    The cursor rides in the body on POST and in the query string on GET, which
    is Notion's split, not a choice made here.
    """
    cursor = None
    while True:
        if method == "POST":
            payload = dict(body or {})
            payload["page_size"] = PAGE_SIZE
            if cursor:
                payload["start_cursor"] = cursor
            page = _request(path, method="POST", body=payload)
        else:
            qs = f"?page_size={PAGE_SIZE}"
            if cursor:
                qs += f"&start_cursor={urllib.parse.quote(cursor)}"
            page = _request(path + qs)

        yield from page.get("results", [])
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return


# ────────────────────────────── shape ──────────────────────────────


def _norm_id(raw: str) -> str:
    """Dashless, so a page fetched through the API and the same page read out of
    an export filename carry ONE id. That is what lets an instance switch roads
    without landing every note a second time."""
    return (raw or "").replace("-", "").lower()


def title_of(obj: dict) -> str:
    """A page's title, wherever Notion put it.

    A database row's title property can be named anything the owner renamed it
    to, so it is found by TYPE. A database object keeps its title at the top
    level instead of in properties, which is a different shape for the same idea.
    """
    if obj.get("object") == "database":
        return blockrender.rich_text(obj.get("title") or [])
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            return blockrender.rich_text(prop.get("title") or [])
    return ""


def _prop_text(prop: dict) -> str:
    kind = prop.get("type", "")
    value = prop.get(kind)
    if kind == "rich_text":
        return blockrender.rich_text(value or [])
    if kind in ("number", "url", "email", "phone_number"):
        return "" if value is None else str(value)
    if kind in ("select", "status"):
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return ", ".join(v.get("name", "") for v in value or [])
    if kind == "checkbox":
        return "Yes" if value else "No"
    if kind == "date":
        if not value:
            return ""
        start, end = value.get("start", ""), value.get("end")
        return f"{start} - {end}" if end else start
    if kind == "people":
        return ", ".join(v.get("name", "") for v in value or [] if v.get("name"))
    if kind == "files":
        return ", ".join(v.get("name", "") for v in value or [])
    if kind == "unique_id":
        prefix = (value or {}).get("prefix") or ""
        return f"{prefix}-{(value or {}).get('number')}" if value else ""
    if kind == "formula":
        inner = value or {}
        return _prop_text({"type": inner.get("type", ""), inner.get("type", ""): inner.get(inner.get("type", ""))})
    return ""


def properties_block(obj: dict) -> str:
    """The non-title properties, as the `Name: value` run Notion's own export
    writes above the body.

    For a database row this IS the content — a reading log's row is a rating, a
    date and a link, and a body-only import would land it empty. Rendered in a
    stable key order so a re-import of an unchanged row is byte-identical.
    """
    lines = []
    for name, prop in sorted((obj.get("properties") or {}).items()):
        kind = prop.get("type", "")
        if kind == "title" or kind in _ENVELOPE_PROPS:
            continue
        text = _prop_text(prop).strip()
        if text:
            lines.append(f"{name}: {text}")
    return "\n".join(lines)


def fetch_blocks(block_id: str, depth: int = 0) -> list:
    """A block subtree, children attached inline as `_children`.

    Depth-capped. Notion allows deep nesting and a cycle is not supposed to be
    possible, but this walks somebody else's data over a network and an
    unbounded recursion there is an outage rather than a bug.
    """
    if depth > 6:
        return []
    out = []
    for block in _paged(f"/blocks/{block_id}/children"):
        if block.get("has_children") and block.get("type") not in ("child_page", "child_database"):
            block["_children"] = fetch_blocks(block["id"], depth + 1)
        out.append(block)
    return out


def _folder_of(page_id: str, index: dict[str, dict]) -> str:
    """The page's ancestry, as `Reading/Books`, built from the search results.

    No extra requests: search already returned every connected page and database,
    so the chain is walked in memory. A chain that leaves the connected set stops
    there — a parent this integration cannot see is not a folder it can name.

    A page nested inside a toggle or a column has a `block_id` parent, which
    would cost a request each to resolve. Those stop the chain too, so such a
    page lands one level shallower than it sits. It is never given a WRONG
    folder, only a shorter one, and short means closer to unfiled, which means
    closer to held.
    """
    parts: list[str] = []
    seen: set[str] = set()
    cur = index.get(page_id, {}).get("parent")
    while cur and cur not in seen:
        seen.add(cur)
        node = index.get(cur)
        if not node:
            break
        parts.append(node["title"] or "Untitled")
        cur = node.get("parent")
    return "/".join(reversed(parts))


def _index(objects: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for obj in objects:
        parent = obj.get("parent") or {}
        parent_id = parent.get("page_id") or parent.get("database_id")
        index[_norm_id(obj.get("id", ""))] = {
            "title": title_of(obj),
            "parent": _norm_id(parent_id) if parent_id else None,
        }
    return index


def _same_minute(edited: str, now: str | None = None) -> bool:
    """Is `last_edited_time` inside the minute this read is happening in?

    Compared as strings deliberately. Both are ISO-8601 UTC from the same API
    contract, so `YYYY-MM-DDTHH:MM` is a total order and a prefix comparison
    cannot fail on a format this has not met — which a parse could.
    """
    if not edited:
        return False
    stamp = now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
    return edited[:16] == stamp[:16]


def read(seen: dict[str, dict] | None = None) -> list[SourceNote]:
    """Every connected page, as notes. `seen` is what is already landed."""
    seen = seen or {}
    everything = list(_paged("/search", method="POST", body={
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    }))
    index = _index(everything)

    out: list[SourceNote] = []
    reused = 0
    skipped_kinds: dict[str, int] = {}
    # A database OBJECT is a container, not writing — its rows come back from
    # search as pages in their own right. Importing it too would land an empty
    # note named after the database beside every row it holds.
    pages = [o for o in everything if o.get("object") == "page"]

    for page in sorted(pages, key=lambda p: p.get("id", "")):
        pid = _norm_id(page.get("id", ""))
        edited = page.get("last_edited_time", "")
        title = title_of(page)
        folder = _folder_of(pid, index)

        landed = seen.get(pid)
        if landed and landed.get("edited") and landed["edited"] == edited:
            # Unchanged since it was landed. Hand back what is on disk rather
            # than nothing: `land()` decides new/changed/same by comparing whole
            # files, so an omitted note is not "unchanged", it is absent, and the
            # counts would report every settled page as skipped forever.
            reused += 1
            out.append(SourceNote(
                external_id=pid, title=landed.get("title") or title,
                body=landed["_body"], created=landed.get("created", ""),
                folder=landed.get("folder", ""), extra={"edited": edited}))
            continue

        body, skipped = blockrender.render(fetch_blocks(page["id"]))
        for kind in skipped:
            skipped_kinds[kind] = skipped_kinds.get(kind, 0) + 1
        props = properties_block(page)
        full = f"{props}\n\n{body}".strip() if props else body
        if page.get("archived") or page.get("in_trash"):
            continue

        out.append(SourceNote(
            external_id=pid,
            title=title or "Untitled",
            body=full,
            created=(page.get("created_time") or "")[:10],
            folder=folder,
            # An edit made in the SAME MINUTE as this read, after this read, is
            # invisible: `last_edited_time` has minute granularity, so the
            # timestamp does not move and every later run compares equal and
            # skips the page forever.
            #
            # Recording no watermark for those pages forces one re-read next
            # run, because the reuse test above requires a truthy `edited`. It
            # costs one page fetch for a page edited in the minute we happened
            # to read it, and it is the difference between a lost edit and a
            # slightly slower run.
            #
            # This matters more the OFTENER you poll, which is the opposite of
            # the intuition: at fifteen-minute checks the window is entered
            # ninety-six times a day instead of once a night (ADR-0015 §7).
            extra={"edited": "" if _same_minute(edited) else edited},
        ))

    print(f"  notion: {len(pages)} connected page(s), {len(pages) - reused} read, "
          f"{reused} unchanged since last import")
    if skipped_kinds:
        # Named, not counted in aggregate. "3 blocks skipped" tells you nothing
        # you can act on; "3 unsupported, 1 table_of_contents" tells you whether
        # any prose was lost.
        detail = ", ".join(f"{k} x{n}" for k, n in sorted(skipped_kinds.items()))
        print(f"  notion: block types with no markdown spelling, dropped: {detail}")
    return out
