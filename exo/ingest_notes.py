"""Apple Notes → Exo-owned note files. The retirement of second-brain's `sync.py`.

Reads the local NoteStore.sqlite through the vendored decoder (`exo/applenotes/`,
copied so Exo OWNS the decode — no cross-repo dependency) and renders each note to
`config.NOTES/raw/import/*.md`, with the SAME frontmatter `t1_index` already reads
(`type: raw`, created, uuid, folder, title). So once this has populated the tree, flip the
note source with `WH_SECOND_BRAIN_DATA=<exo>/notes` and `t1_notes` is Exo-owned.

Needs Full Disk Access to read NoteStore.sqlite (same requirement as sync.py). Run it where
FDA is granted (the Life Terminal app, or an FDA terminal) — it can't read the DB otherwise.

Identity is by UUID: existing files are matched on their `uuid:` frontmatter, so a re-run is
idempotent (same → skip, changed body → overwrite, new note → new file). The append-in-place /
fork / supersede version model (ADR-0007) is a deliberate FOLLOW-ON — this v1 gets the record
into Exo's custody; frozen-version history is a refinement to port from sync.py next.

    wh ingest-notes            # Apple Notes → <exo>/notes/raw/import
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .applenotes.extract import extract_notes


def _yaml_str(s: str) -> str:
    """Single-line YAML scalar. Collapse line separators (Apple Notes emits U+2028 inside a
    title's first line, which PyYAML reads as a line break — an unquoted title then splits
    into a second key and every fail-closed reader rejects the file)."""
    if not s:
        return '""'
    s = re.sub("[\r\n\u2028\u2029]+", " ", s).strip()
    if not s:
        return '""'
    if any(c in s for c in (':', '#', '[', ']', '{', '}', '&', '*', '!', '|',
                            '>', "'", '"', '%', '@', '`')):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "note").lower()).strip("-")[:60]
    return s or "note"


def _render(note, imported: str) -> str:
    created = (note.created or "")[:10]
    body = (note.structured_body or "").strip() or (note.body or "").strip()
    fm = [
        "---",
        "type: raw",
        f"created: {created}",
        f"imported: {imported}",
        "source: apple-notes",
        f"uuid: {note.uuid}",
        f"folder: {_yaml_str(note.folder or 'Unfiled')}",
        f"title: {_yaml_str(note.title)}",
        "---",
    ]
    return "\n".join(fm) + "\n\n" + body + "\n"


_UUID_RE = re.compile(r"^uuid:\s*(\S+)\s*$", re.M)


def _existing_by_uuid(out: Path) -> dict[str, Path]:
    """Map each already-written note's uuid → its file, so identity is by uuid not filename."""
    index: dict[str, Path] = {}
    if not out.exists():
        return index
    for p in out.glob("*.md"):
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            continue
        m = _UUID_RE.search(head)
        if m:
            index[m.group(1)] = p
    return index


def _new_path(out: Path, note, taken: set[str]) -> Path:
    base = f"{(note.created or '')[:10]}-{_slug(note.title)}"
    name = base + ".md"
    if name in taken:  # deterministic disambiguation by uuid tail
        name = f"{base}-{note.uuid[:8].lower()}.md"
    taken.add(name)
    return out / name


def run(db_path: Path | None = None) -> dict:
    notes, skipped = extract_notes(db_path) if db_path else extract_notes()
    out = config.NOTES / "raw" / "import"
    out.mkdir(parents=True, exist_ok=True)
    imported = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_uuid = _existing_by_uuid(out)
    taken = {p.name for p in by_uuid.values()}
    counts = {"new": 0, "same": 0, "changed": 0, "skipped": len(skipped)}
    for note in notes:
        content = _render(note, imported)
        p = by_uuid.get(note.uuid)
        if p is None:
            p = _new_path(out, note, taken)
            counts["new"] += 1
        else:
            try:
                if p.read_text(encoding="utf-8") == content:
                    counts["same"] += 1
                    continue
            except OSError:
                pass
            counts["changed"] += 1
        p.write_text(content, encoding="utf-8")
    print(f"  notes → {out}")
    print(f"  new {counts['new']}   changed {counts['changed']}   same {counts['same']}   "
          f"skipped {counts['skipped']} (excluded/encrypted/empty)")
    return counts
