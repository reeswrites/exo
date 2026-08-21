"""Files — a directory of text, a single file, or a blob on standard input.

This is the adapter that makes the framework worth having. Most writing does not
live in a product with an export format: it is a folder of markdown, a dump out
of some app that only knew how to write .txt, a directory someone rsynced off a
dead laptop, or a paragraph you want in the record right now with no file at all.
All of that is the same shape — bytes that are text, with a name and a date — and
none of it needs its own ingester.

**An Obsidian vault is one of these**, and deliberately does not get an adapter
of its own: a vault IS a directory of markdown, which is the format the engine
has claimed to read since ADR-0001. What it needed was for this module to be
right about the four things a vault does that a loose pile of files does not —
dot-directories full of machinery, binary attachments beside the prose, daily
notes named for nothing but their date, and `tags:`/`aliases:` properties that
are the whole organising signal. Each is handled below and tested against a
vault-shaped fixture. Anything else that exports markdown — Bear, Ulysses, iA
Writer, a Logseq graph — lands the same way.

    exo ingest-notes files --from ~/writing        a tree
    exo ingest-notes files --from notes.md         one file
    pbpaste | exo ingest-notes files --from -     a blob, titled by its first line

## Identity

For a file, identity is its path relative to the root you named. That is the
only handle a folder of files has, and it is a real one: move a file and it
becomes a different note, which is honest, because in a folder of files moving a
file IS how you change what a note is.

For a blob on stdin there is no path, so identity is the hash of the text. Piping
the same paragraph twice lands one note rather than two, which is the behaviour
you want from something you will inevitably run twice.

## The folder axis

A file's folder is its directory, relative to the root. A file sitting at the
root has no folder, so it lands unfiled — and unfiled is held (ADR-0009). That
is the correct default for a pile of loose text nobody has sorted: the folder
axis exists to record a decision, and dumping a directory is not one.

## What is refused

Anything holding a NUL byte. `io.markdown` learned this from macOS AppleDouble
sidecars — `._name.md`, hidden on the Mac, real 163-byte binary files the moment
the archive is opened on Linux, and they match `*.md`. A pointed-at directory is
a worse hazard than a mirrored one, because nobody curated it. Dotfiles go the
same way, and a suffix this module does not know is skipped rather than guessed
at: a .pdf read as latin-1 is not a note, it is noise with a title.
"""
from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import SourceNote

LANDING = "files"
SOURCE = "files"

# Suffixes whose bytes are text a person wrote. Deliberately short: adding one
# is a decision about what counts as a note, and the failure mode of guessing is
# a corpus full of machine output nobody meant to keep.
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".mdown"}

# Repo and export furniture, not writing. Same list `drafts` refuses, plus the
# index files an export tool leaves behind.
NOT_NOTES = {"README.md", "LICENSE.md", "CONTRIBUTING.md", "CHANGELOG.md", "index.md"}

# A date as the WHOLE filename, or as a prefix before a separator. Obsidian's
# daily notes are named `2026-02-03.md` exactly, and requiring a separator after
# the date missed the single most common filename pattern in a markdown vault —
# every daily note landed dated the day it was imported.
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ _-]+|$)")
_FM_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_ -]*):\s*(.*)$")
_FM_ITEM = re.compile(r"^-\s+(.*)$")


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse frontmatter by hand rather than with yaml, for the same reason
    `drafts` does: a half-typed value in one file must not be able to fail the
    whole ingest.

    Block sequences are folded into a comma-joined string:

        tags:            ->   {"tags": "reading, systems"}
          - reading
          - systems

    Obsidian writes tags and aliases that way, and they are the primary
    organising signal in a vault that has them. Comma-joining rather than
    keeping a list is not a compromise — `t1_index._s` flattens every list-valued
    frontmatter value to exactly this shape already, so the string IS what the
    record would have held.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm: dict[str, str] = {}
    key = None
    items: list[str] = []

    def flush():
        if key is not None and items:
            fm[key] = ", ".join(items)

    for raw in text[3:end].splitlines():
        line = raw.strip()
        if not line:
            continue
        item = _FM_ITEM.match(line)
        if item and key is not None:
            items.append(_flow(_unquote(item.group(1))))
            continue
        m = _FM_LINE.match(line)
        if not m:
            continue
        flush()
        key, items = m.group(1).strip().lower(), []
        value = _flow(_unquote(m.group(2).strip()))
        if value:
            fm[key] = value
            key, items = None, []
    flush()
    return fm, text[end + 4:].lstrip("\n")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _flow(value: str) -> str:
    """`[a, "b c"]` -> `a, b c`. Obsidian writes tags both ways depending on
    whether they were typed in the editor or in the properties panel, and a
    vault holding both styles would otherwise land two different shapes for the
    same field."""
    if not (value.startswith("[") and value.endswith("]")):
        return value
    inner = value[1:-1].strip()
    if not inner:
        return ""
    parts = [_unquote(p.strip()) for p in inner.split(",")]
    return ", ".join(p for p in parts if p)


def _first_heading(body: str) -> str:
    m = re.search(r"^#{1,6}\s+(.+?)\s*$", body, re.M)
    return m.group(1).strip() if m else ""


def _title_from_stem(stem: str) -> str:
    """`2026-08-01-the-caching-argument` -> `the caching argument`.

    The date prefix is a filing convention, not part of the title, and it is
    already captured as `created` — leaving it in means every note in a
    date-prefixed tree is titled with its own date twice.
    """
    return _DATE_PREFIX.sub("", stem).replace("_", " ").replace("-", " ").strip() or stem


def _read_text(path: Path) -> str | None:
    """The file's text, or None if it is not text after all.

    `errors="strict"`, unlike everywhere else in the engine that reads a file it
    already trusts. A pointed-at directory has not been curated, and a file that
    is not valid UTF-8 is far more likely to be a binary that matched a suffix
    than a note in another encoding — and the replacement-character version of a
    binary is a note-shaped object full of garbage that will be embedded,
    published and searched forever.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return None if "\x00" in text else text


# The frontmatter keys the note contract owns. Anything else a source file
# carries is passed through; these are re-derived and must not be shadowed.
_CONTRACT_KEYS = {"type", "created", "imported", "source", "uuid", "folder", "title", "date"}


def _created(path: Path, fm: dict[str, str], stem: str) -> str:
    """When the note was written, in the order those answers can be trusted.

    Frontmatter first: it is the only one the author stated. Then a date in the
    filename. Then the file's BIRTH time where the platform records one — on a
    vault that has been through iCloud, Dropbox or a fresh `git clone`, mtime is
    the date of the sync rather than of the writing, and birthtime survives at
    least the first two. Then mtime, which is a bad answer and still better than
    no date at all (`t1_index.open_threads` reaches for it for the same reason).
    """
    stated = (fm.get("created") or fm.get("date") or "")[:10]
    if stated:
        return stated
    m = _DATE_PREFIX.match(stem)
    if m:
        return m.group(1)
    st = path.stat()
    when = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%d")


def _one(path: Path, root: Path) -> SourceNote | None:
    text = _read_text(path)
    if text is None:
        return None
    fm, body = _split_frontmatter(text)
    rel = path.relative_to(root)
    stem = path.stem

    title = fm.get("title") or _first_heading(body) or _title_from_stem(stem)
    folder = fm.get("folder") or str(rel.parent).replace("\\", "/")
    return SourceNote(
        external_id=rel.as_posix(),
        title=title,
        body=body,
        created=_created(path, fm, stem),
        folder="" if folder in (".", "") else folder,
        # A tree may already carry the distinction the record cares about — a
        # `refined/` directory of finished prose beside a `raw/` one of thinking.
        # Honour a declared `type:`; never infer one from a directory name, which
        # would make an unrelated folder called `refined` change what its notes
        # are taken to be.
        note_type=fm.get("type") or "raw",
        # Everything else the file declared, carried through verbatim. A vault's
        # tags and aliases are its organising signal, and landing a note without
        # them makes Exo's copy worse than the original — which is the one thing
        # the copy may never be, since it is the copy that outlives the app.
        #
        # They reach the FILE, not the t1_notes payload. Adding a payload column
        # re-mints every note id (CONTRIBUTING), so folding tags into the record
        # is its own decision, taken deliberately or not at all. Until then they
        # are on disk, greppable, and lost by nothing.
        extra={k: v for k, v in sorted(fm.items())
               if k not in _CONTRACT_KEYS and v},
    )


def _stdin() -> list[SourceNote]:
    text = sys.stdin.read()
    if "\x00" in text or not text.strip():
        return []
    body = text.strip()
    # No path, so identity is the text. Piping the same paragraph twice lands
    # one note; piping an edited version lands a second, which is right — a blob
    # with no name has no way to claim it is a revision of anything.
    ident = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return [SourceNote(
        external_id=f"sha256:{ident}",
        title=_first_heading(body) or body.split("\n", 1)[0][:80].strip(),
        body=body,
        created=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        folder="",   # a blob is unfiled by definition, and unfiled is held
    )]


def read(src: str | None = None, seen: dict | None = None) -> list[SourceNote]:
    """`seen` is ignored — reading a local file is cheaper than deciding not to."""
    if src == "-":
        return _stdin()
    if not src:
        raise ValueError(
            "the files source needs somewhere to read from: "
            "--from <dir|file|-> , or a [notes.sources] entry in exo.toml")

    root = Path(src).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"no such path: {root}")
    if root.is_file():
        note = _one(root, root.parent)
        return [note] if note else []

    out: list[SourceNote] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.name.startswith(".") or path.name in NOT_NOTES:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue  # .git, .obsidian, .trash — machinery, not writing
        note = _one(path, root)
        if note:
            out.append(note)
    return out
