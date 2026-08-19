"""t1_draft — longform writing in progress, from md-writer.

The store had the two ends of a piece of writing and not the middle. A private
note in the vault is thinking; a post on the blog is the finished argument
(ADR-0012). What was missing is the state between them: the thing being written,
which is exactly what someone forgets they started.

Read from the DIRECTORY, not from git. `_docs_for` in the project scanner walks
`git ls-files`, so it only ever sees committed files — and the draft you are in
the middle of is the one most worth surfacing, precisely because it is the one
you might abandon. Drafts are committed by the NIGHTLY rather than as they are
written, so at any moment during the day the newest work is uncommitted — which
makes git precisely the wrong source for it.

The repo ALSO appears on the workbench through `wh scan-projects`, like any
other repo under ~/Documents. Same directory, two readings: the scanner sees a
project with commits, this sees the prose.

`modified` is the field that earns its place. "Which drafts have gone cold" is
the question a writer cannot answer about themselves, and it is one subtraction
away here.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import config
from ..provenance import Row, stable_id

# Repo furniture, not writing. md-writer's server excludes the same names.
NOT_DRAFTS = {"README.md", "LICENSE.md", "CONTRIBUTING.md", "CHANGELOG.md"}
BODY_CAP = 40_000          # a draft is prose; longer than this is a book


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """md-writer writes Jekyll frontmatter, so a finished draft moves into
    _posts/ unchanged. Parsed by hand rather than with yaml: the fields are
    known, and a half-typed value must not be able to fail an ingest."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line.strip())
        if not m:
            continue
        v = m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1].replace("''", "'")
        fm[m.group(1)] = v
    return fm, text[end + 4:].lstrip("\n")


def _first_heading(body: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    return m.group(1).strip() if m else ""


def load() -> list[Row]:
    src = config.DRAFTS
    if not src.exists():
        print("  drafts: no raw/drafts — run `wh sync-raw`")
        return []
    rows: list[Row] = []
    for path in sorted(src.glob("*.md")):
        if path.name in NOT_DRAFTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            st = path.stat()
        except OSError:
            continue
        fm, body = _split_frontmatter(text)
        body = body.strip()
        title = fm.get("title") or _first_heading(body) or path.stem
        # The filename carries the date it was started; mtime carries the last
        # time it was touched. Both matter, and neither substitutes for the other.
        m = re.match(r"^(\d{4}-\d{2}-\d{2})-", path.stem)
        started = m.group(1) if m else None
        modified = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%d")
        words = len(body.split())
        rows.append(Row(
            tier="t1", zone="draft", source="md-writer", author="human",
            created=started, origin_ref=path.stem,
            id=stable_id("drafts", path.stem),
            payload={
                "slug": path.stem,
                "title": title,
                "description": fm.get("description", ""),
                "started": started or "",
                "modified": modified,
                "words": words,
                # An empty draft is a title someone typed and walked away from.
                # Worth keeping — that is a intention, and forgetting it is the
                # problem this store exists to solve — but worth labelling.
                "state": "empty" if words < 5 else "drafting",
                "body": body[:BODY_CAP],
            },
        ))
    return rows
