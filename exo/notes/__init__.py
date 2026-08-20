"""Notes ingestion — the part of the record you write by hand, from wherever you write it.

Exo had one note ingester and it was Apple Notes: `ingest_notes.py` read a
NoteStore.sqlite, rendered markdown, and wrote it into `notes/raw/import/`. All
four steps in one file, three of which had nothing to do with Apple.

The four steps, separated:

    reach       get at the source          ← the ONLY source-specific part
    normalize   → SourceNote               ← a title, a body, an id, a folder
    land        → an Exo-owned .md file    ← this module
    index       → t1_notes                 ← loaders/t1_index.py, unchanged

So a note source is an **adapter**: something that yields `SourceNote`s. Apple
Notes is one. A Notion export is one. A directory of text files is one. Standard
input is one. Everything downstream — atoms, vectors, the two publication axes,
the read surface — is already generic and never learns which it was.

## The note file IS the contract

A landed note is markdown with the frontmatter `t1_index` already reads:

    ---
    type: raw            # which half of the tree; `raw` is thinking, `refined` is prose
    created: 2026-08-01  # when it was written, not when it arrived
    imported: 2026-08-20 # when it arrived
    source: notion       # which silo it came out of — DATA, not branding (CONTEXT)
    uuid: <source id>    # identity, so a re-import is idempotent
    folder: Reading      # the FOLDER AXIS of publication
    title: ...
    ---

Files, not rows, because the file is what survives this system. You can grep it,
diff it, put it under git, hand it to a different tool in ten years, and restore
it without Exo existing. The parquet under `zones/t1` is a projection of these
and is thrown away on every rebuild; these are the thing itself.

## Identity, and why a re-import is free

Identity is `(source, uuid)`, matched by reading the `uuid:` line back out of the
files already landed. Same note, unchanged body → skipped. Same note, edited →
overwritten in place. New note → new file. Nothing is ever deleted here: a note
withdrawn upstream stays landed, because a silo losing your writing is not your
decision to have made.

Re-importing costs nothing downstream either. Vectors are keyed on
`sha256(text)` (`exo/embed.py`), so a landed note whose text is unchanged reuses
its vector even if its id, its path and its source all changed.

## Landing a source is a publication decision, and it fails closed

Publication gates a note on two axes, and both are carried by the landed file:
its `folder:` (the folder axis) and its position in the tree (the path axis,
matched by longest declared prefix in `serve-manifest.json`). A source that
landed inside an already-declared zone would inherit that zone's decision —
`raw/import` serves, so `raw/import/notion/` would serve, silently, on the first
run of a new adapter. So **every source gets its own top-level landing zone**,
`raw/<landing>/`, which no declared prefix covers until someone declares it. The
first publish after adding a source fails with "note(s) under no declared
path_zone", which is the correct answer to "should this be on the internet?"
from a system that was never asked.

Apple Notes keeps `raw/import` rather than moving to `raw/apple-notes`: an
atom's id hashes its note's `origin_ref` (`t2.atomize`), so renaming that
directory re-mints every atom already stored. Stored identity, same rule as a
`source=` string (CONTRIBUTING).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import config


@dataclass
class SourceNote:
    """One note, as an adapter hands it over. The whole interface.

    `external_id` is the source's own identity for the note — an Apple UUID, a
    Notion page id, a relative path. It must be stable across runs and unique
    within the source; nothing else about the note has to be either.

    `folder` is the folder axis of publication, so an adapter answering it
    carelessly is answering a privacy question carelessly. The honest answer for
    a source with no folder concept is `""` — the unfiled drawer, which is held
    (ADR-0009), because a note nobody filed is a note nobody decided about.
    """

    external_id: str
    title: str
    body: str
    created: str = ""                    # YYYY-MM-DD, or "" if the source has none
    folder: str = ""                     # the folder axis; "" = unfiled = held
    note_type: str = "raw"               # raw (thinking) | refined (prose)
    extra: dict[str, str] = field(default_factory=dict)  # extra frontmatter lines


# ────────────────────────────── rendering ──────────────────────────────


def _yaml_str(s: str) -> str:
    """Single-line YAML scalar. Collapse line separators (Apple Notes emits
    U+2028 inside a title's first line, which PyYAML reads as a line break — an
    unquoted title then splits into a second key and every fail-closed reader
    rejects the file)."""
    if not s:
        return '""'
    s = re.sub("[\r\n\u2028\u2029]+", " ", s).strip()
    if not s:
        return '""'
    if any(c in s for c in (':', '#', '[', ']', '{', '}', '&', '*', '!', '|',
                            '>', "'", '"', '%', '@', '`')):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "note").lower()).strip("-")[:60]
    return s or "note"


def render(note: SourceNote, source: str, imported: str) -> str:
    """The note file, exactly. One writer, so the contract has one definition."""
    lines = [
        "---",
        f"type: {note.note_type or 'raw'}",
        f"created: {(note.created or '')[:10]}",
        f"imported: {imported}",
        f"source: {source}",
        f"uuid: {note.external_id}",
        f"folder: {_yaml_str(note.folder)}",
        f"title: {_yaml_str(note.title)}",
    ]
    for k, v in sorted(note.extra.items()):
        lines.append(f"{k}: {_yaml_str(str(v))}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + (note.body or "").strip() + "\n"


# ────────────────────────────── landing ──────────────────────────────

_UUID_RE = re.compile(r"^uuid:\s*(\S+)\s*$", re.M)

# A NUL means whatever was read is not text. It is caught HERE rather than at
# publish because by then it is three passes downstream: D1 accepts a statement
# containing one, reports success, and leaves the table empty (publish_cf's
# `BinaryValue`). A file that cannot be a note should never become one.
_NOT_TEXT = "\x00"


def _existing_by_uuid(out: Path) -> dict[str, Path]:
    """Map each already-landed note's uuid → its file, so identity is by uuid
    rather than by filename. A retitled note keeps its file."""
    index: dict[str, Path] = {}
    if not out.exists():
        return index
    for p in sorted(out.rglob("*.md")):
        if p.name.startswith("."):
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:1200]
        except OSError:
            continue
        m = _UUID_RE.search(head)
        if m:
            index[m.group(1)] = p
    return index


def _new_path(out: Path, note: SourceNote, taken: set[str]) -> Path:
    """Where a note lands the FIRST time. Never consulted again — identity is
    `uuid`, so a retitled note keeps the file it already has.

    Two notes can still want one filename, and the disambiguation must be a
    function of the note rather than of the order it arrived in: a counter would
    hand `-2` to whichever note the walk reached second, which is a different
    note on a machine whose filesystem sorts differently.
    """
    base = f"{(note.created or '')[:10]}-{slug(note.title)}" if note.created else slug(note.title)
    name = base + ".md"
    if name in taken:
        tail = re.sub(r"[^a-z0-9]+", "-", note.external_id.lower())[:8].strip("-")
        name = f"{base}-{tail}.md" if tail else name
    if name in taken:
        # A path-shaped id (the files and notion adapters use one) shares its
        # first eight characters with every sibling in the same directory, so
        # the tail above collides exactly where collisions are most likely. Hash
        # the whole id rather than a prefix of it: still deterministic, and it
        # cannot collide by being adjacent.
        digest = hashlib.sha256(note.external_id.encode("utf-8")).hexdigest()[:8]
        name = f"{base}-{digest}.md"
    taken.add(name)
    return out / name


def land(notes: list[SourceNote], source: str, landing: str, *,
         root: Path | None = None) -> dict:
    """Write `notes` into `<notes>/raw/<landing>/`, idempotently. Returns counts.

    Idempotent in the sense that matters: run it twice and the second run writes
    nothing, so `exo verify` stays clean and the nightly does not churn a tree
    that is mirrored somewhere else. `same` is the number that should be large.
    """
    out = (root or config.NOTES) / "raw" / landing
    out.mkdir(parents=True, exist_ok=True)
    imported = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_uuid = _existing_by_uuid(out)
    # Every name in the directory, not only the ones carrying a uuid. A file
    # that landed before the frontmatter contract, or was dropped in by hand, is
    # still a filename this run must not write over.
    taken = {p.name for p in out.glob("*.md")}
    counts = {"new": 0, "same": 0, "changed": 0, "skipped": 0}

    seen: set[str] = set()
    for note in notes:
        if not note.external_id:
            counts["skipped"] += 1
            continue
        if note.external_id in seen:
            # Two notes claiming one identity is the adapter's bug, not the
            # writer's to paper over — the second would silently overwrite the
            # first and the count would still read healthy.
            raise ValueError(
                f"{source}: two notes share external_id {note.external_id!r}; "
                "an adapter must give each note a stable, unique id")
        seen.add(note.external_id)
        if _NOT_TEXT in (note.body or "") or _NOT_TEXT in (note.title or ""):
            counts["skipped"] += 1
            continue
        if not (note.body or "").strip():
            counts["skipped"] += 1
            continue

        content = render(note, source, imported)
        p = by_uuid.get(note.external_id)
        if p is None:
            p = _new_path(out, note, taken)
            counts["new"] += 1
        else:
            try:
                if _same_but_for_import(p.read_text(encoding="utf-8"), content):
                    counts["same"] += 1
                    continue
            except OSError:
                pass
            counts["changed"] += 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return counts


_IMPORTED_RE = re.compile(r"^imported: .*$", re.M)


def _same_but_for_import(old: str, new: str) -> bool:
    """Is this the same note, ignoring the day it was last seen?

    `imported:` is the one field that changes without the note changing. Left in
    the comparison, every note rewrites itself on the first run of every new day
    — which makes the tree's mtimes meaningless, re-uploads the whole mirror to
    object storage nightly, and turns `changed` into a count of days rather than
    a count of edits.
    """
    return _IMPORTED_RE.sub("", old) == _IMPORTED_RE.sub("", new)


# ────────────────────────────── running one ──────────────────────────────


def run(source: str | None = None, src: str | None = None) -> dict[str, dict]:
    """Import from one source, or from every source this instance configured.

    Bare `exo ingest-notes` runs what `exo.toml` declares:

        [notes.sources]
        apple  = ""                          # no argument; reads the local database
        notion = "raw/notion-export.zip"     # relative paths resolve against EXO_HOME

    A configured source that raises is not swallowed. A note importer that
    reports success while one of its sources returned nothing is the same
    failure the publication guard exists to catch three steps later, and it is
    much cheaper to see here.
    """
    from . import sources as registry

    targets: list[tuple[str, str | None]]
    if source:
        targets = [(source, src)]
    else:
        configured = config.NOTES_SOURCES
        if not configured:
            raise ValueError(
                "no note sources configured. Either name one — "
                f"`exo ingest-notes <{'|'.join(registry.names())}> --from PATH` — "
                "or declare a [notes.sources] table in exo.toml")
        targets = [(name, config.notes_source_path(name)) for name in sorted(configured)]

    results: dict[str, dict] = {}
    landings: dict[str, str] = {}
    for name, arg in targets:
        adapter = registry.get(name)
        landing = getattr(adapter, "LANDING", name)
        # Two sources sharing a landing directory share an identity namespace:
        # `uuid` is unique within a directory, not globally, so the second
        # source's notes would overwrite the first's in place and both imports
        # would print healthy counts.
        if landing in landings and landings[landing] != name:
            raise ValueError(
                f"note sources {landings[landing]!r} and {name!r} both land in "
                f"raw/{landing} — a landing directory belongs to one source")
        landings[landing] = name

        notes = adapter.read(arg)
        counts = land(notes, getattr(adapter, "SOURCE", name), landing)
        results[name] = counts
        out = config.NOTES / "raw" / landing
        print(f"  {name:8s} -> {out}")
        print(f"    new {counts['new']:>5}   changed {counts['changed']:>5}   "
              f"same {counts['same']:>5}   skipped {counts['skipped']:>5}")
    return results
