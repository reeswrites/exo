"""Notion — the "Export -> Markdown & CSV" archive, as a zip or an unpacked tree.

The export, not the API, and that is a considered choice rather than a stopgap.
An export is a **format**: a zip anyone with a Notion account can produce, which
is what makes an adapter for it belong in the engine at all (CONTRIBUTING). The
API is a place — it needs an integration, a token, and every page individually
shared with that integration, which is a per-workspace act of configuration that
belongs in an instance's `plugins/` if anyone wants it. An export also works
offline, on a machine that never talks to Notion, and reproduces byte-for-byte,
which is what `exo verify` is built on.

    exo ingest-notes notion --from ~/Downloads/Export-1a2b3c.zip
    exo ingest-notes notion --from ~/notion-export/

## What the export looks like

    Export-<uuid>/
      Reading <32hex>.md              a page
      Reading <32hex>/                its subpages, as a directory
        On Caching <32hex>.md
        Sources <32hex>.csv           a database index
        Sources <32hex>/              the database's rows, one .md each
          Row title <32hex>.md

The trailing 32 hex characters are the page id — a dashless UUID, stable for
that page across exports. That is the whole reason this adapter can be
idempotent: export twice, re-ingest twice, and an unedited page is recognised as
the same note rather than landing again under a new filename.

A page with no id in its name (some database rows, some older exports) falls
back to a hash of its path. That is weaker — renaming the page then lands a
second copy — and the adapter says so at import time rather than silently.

The `.csv` files are indexes of the databases whose rows sit beside them as
markdown. Reading both would file every row twice, once as prose and once as a
comma. They are skipped.

## The folder axis

A page's folder is its position in the export's page hierarchy, ids stripped:
`Reading/Books`. Nesting deeper gives a longer folder string, and each distinct
one needs its own decision in `serve-manifest.json` — the manifest matches
folders exactly, so `Reading` serving says nothing about `Reading/Books`. That is
the fail-closed behaviour, not a gap in it: a subpage is not covered by a
decision made about its parent.

A top-level page has no folder and lands unfiled, which is held (ADR-0009).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .. import SourceNote

LANDING = "notion"
SOURCE = "notion"

# The dashless UUID Notion appends to every exported page's filename, and the
# dashed form older exports used.
_PAGE_ID = re.compile(
    r"[ _-]+("
    r"[0-9a-f]{32}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r")$"
)

# A Notion property line in the block that follows the page title.
_PROPERTY = re.compile(r"^([^:\n]{1,60}):[ \t]+(.+)$")

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

_ISO = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_LONG = re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b")

# Property names that mean "when this was written". Notion localises none of
# these, and a workspace that renamed its date property simply gets the file's
# own date instead, which is a worse answer but never a wrong one.
_DATE_KEYS = ("created", "created time", "date", "created at", "published")


def _strip_id(name: str) -> tuple[str, str]:
    """`Reading a1b2...f0` -> (`Reading`, `a1b2...f0`). No id -> ("", name)."""
    m = _PAGE_ID.search(name)
    if not m:
        return name, ""
    return name[: m.start()].strip(), m.group(1).replace("-", "")


def _parse_date(value: str) -> str:
    m = _ISO.search(value)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = _LONG.search(value)
    if m:
        mon, d, y = m.groups()
        n = _MONTHS.get(mon.lower())
        if n:
            return f"{y}-{n:02d}-{int(d):02d}"
    return ""


def _parse(text: str) -> tuple[str, str, str]:
    """(title, created, body) out of one exported page.

    Notion writes the title as a single `# ` heading, then a run of
    `Property: value` lines, then a blank line, then the page. The property run
    is LEFT IN THE BODY: those values are content a person can see in Notion,
    dropping them would be a lossy import, and the atomizer discards a span of
    fewer than five words anyway (`t2._meaningful`), so a `Tags: a, b` line
    costs nothing downstream. Only the date is lifted out, because `created` is
    a field of the record rather than a line of prose.
    """
    lines = text.split("\n")
    title = ""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        title = lines[i][2:].strip()
        i += 1

    created = ""
    j = i
    # Notion puts a blank line between the title and the property block, and
    # another between the block and the page. Skipping the first is what makes
    # the run findable at all; the second is what ends it.
    while j < len(lines) and not lines[j].strip():
        j += 1
    while j < len(lines) and lines[j].strip():
        m = _PROPERTY.match(lines[j].strip())
        if not m:
            break
        key, value = m.group(1).strip().lower(), m.group(2)
        if not created and key in _DATE_KEYS:
            created = _parse_date(value)
        j += 1

    return title, created, "\n".join(lines[i:]).strip()


def _root_of(base: Path) -> Path:
    """Descend past the export's wrapper directory.

    A Notion zip unpacks to a single `Export-<uuid>/` (or `Private & Shared/`)
    holding everything. Left in place it becomes the first folder segment of
    every note, so every folder decision in the manifest would carry a directory
    name containing a fresh uuid — which changes on every export, so every
    decision would expire the next time you exported.
    """
    cur = base
    for _ in range(3):
        entries = [p for p in cur.iterdir() if not p.name.startswith(".")]
        subdirs = [p for p in entries if p.is_dir()]
        if len(subdirs) == 1 and not any(p.suffix.lower() == ".md" for p in entries):
            cur = subdirs[0]
            continue
        break
    return cur


def _folder(rel: Path) -> str:
    """The page hierarchy above this page, ids stripped: `Reading/Books`."""
    parts = [_strip_id(p)[0] or p for p in rel.parent.parts if p not in (".", "")]
    return "/".join(parts)


def _walk(root: Path) -> list[SourceNote]:
    out: list[SourceNote] = []
    unidentified = 0
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("."):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in text:
            continue
        rel = path.relative_to(root)
        stem_title, page_id = _strip_id(path.stem)
        if not page_id:
            unidentified += 1
            page_id = "path:" + hashlib.sha256(
                rel.as_posix().encode("utf-8")).hexdigest()[:16]
        title, created, body = _parse(text)
        out.append(SourceNote(
            external_id=page_id,
            title=title or stem_title or path.stem,
            body=body,
            created=created,
            folder=_folder(rel),
        ))
    if unidentified:
        # Loud, because the consequence is invisible from here and arrives one
        # export later: these notes are keyed on their path, so renaming or
        # moving the page lands a second copy of it rather than updating the
        # first, and nothing downstream can tell the two apart.
        print(f"  notion: WARNING {unidentified} page(s) carry no id in their "
              "filename and are keyed on their path — renaming one will land a "
              "duplicate on the next import")
    return out


def read(src: str | None = None) -> list[SourceNote]:
    if not src:
        raise ValueError(
            "the notion source needs the export: --from <export.zip|dir>, "
            "or a [notes.sources] entry in exo.toml")
    path = Path(src).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"no such path: {path}")

    if path.is_file():
        if path.suffix.lower() != ".zip":
            raise ValueError(f"expected a Notion export .zip or a directory, got {path.name}")
        tmp = Path(tempfile.mkdtemp(prefix="exo-notion-"))
        try:
            with zipfile.ZipFile(path) as z:
                # Extract by hand rather than extractall: a zip entry may name
                # `../` and write outside the destination, and the archive here
                # is a download. Python 3.12 refuses some of these and not all.
                for member in z.namelist():
                    dest = (tmp / member).resolve()
                    if not str(dest).startswith(str(tmp.resolve())):
                        raise ValueError(f"refusing zip entry outside the archive: {member}")
                z.extractall(tmp)
            return _walk(_root_of(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return _walk(_root_of(path))
