"""Apple Notes — the local NoteStore.sqlite, through the vendored decoder.

The decoder lives in `exo/applenotes/`, copied into this repo so Exo OWNS the
decode rather than depending on another checkout for it. Nothing here parses
anything; this is only the shape change from `NoteRecord` to `SourceNote`.

Needs Full Disk Access to read the database. Run it where that is granted — it
cannot read the file otherwise, and the failure is a PermissionError rather than
an empty result, which is the right way round.

LANDING is `import`, not `apple-notes`, and that is not tidiness deferred. An
atom's id hashes its note's `origin_ref` (`t2.atomize`), so renaming the
directory these land in re-mints every atom already stored — a life's worth of
rows read as new by the ledger and announced as recently added by the surface.
Stored identity, exactly like a `source=` string (CONTRIBUTING).
"""
from __future__ import annotations

from pathlib import Path

from .. import SourceNote

LANDING = "import"
SOURCE = "apple-notes"


def read(src: str | None = None, seen: dict | None = None) -> list[SourceNote]:
    """Every note in the database, minus the ones the decoder refused.

    `src` is an optional path to a NoteStore.sqlite — a copy, a backup, a
    fixture. Absent, the decoder looks where macOS keeps the live one.

    `seen` is ignored. The database is on this disk and the decode is local, so
    working out what has changed would cost more than reading it.
    """
    from ...applenotes.extract import extract_notes

    notes, _skipped = extract_notes(Path(src)) if src else extract_notes()
    out: list[SourceNote] = []
    for n in notes:
        # structured_body carries the markdown the decoder reconstructed from
        # Apple's attribute runs; body is the flat fallback for a note whose
        # structure did not survive. Prefer the former, never lose the latter.
        body = (n.structured_body or "").strip() or (n.body or "").strip()
        out.append(SourceNote(
            external_id=n.uuid,
            title=n.title,
            body=body,
            created=(n.created or "")[:10],
            # Apple's default drawer is literally called Notes, and a note that
            # was never filed sits in it. Passing that through as a folder name
            # would make "not filed" indistinguishable from "filed in Notes" —
            # ADR-0009 held that drawer for a reason, so it keeps its name and
            # the manifest decides. Only a note with no folder AT ALL is unfiled.
            folder=n.folder or "",
        ))
    return out
