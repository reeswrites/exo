"""The adapter registry — one entry per place notes come out of.

An adapter is a module with two names:

    LANDING   the directory under `notes/raw/` its notes land in. Its own, so a
              new source cannot inherit an existing zone's publication decision
              (see `exo.notes`).
    read(src) -> list[SourceNote]     `src` is whatever `--from` gave, or None.

That is the whole interface. Nothing an adapter does can reach past it: it
cannot choose an id scheme, a filename, a frontmatter field or a landing path,
because those are the contract and the contract has one implementation.

## Which adapters ship in the engine

The rule is CONTRIBUTING's: a loader ships here if its input is a **format**,
and in an instance's `plugins/` if its input is a **place**. All three of these
are formats — a NoteStore.sqlite, a Notion export, a directory of text files.
Anyone can hold one. An adapter for *your* team's wiki is yours.

Instances add their own the same way plugins add loaders: `exo.plugins`
registers them, and they get the same landing rule and the same fail-closed
publication consequence as these.
"""
from __future__ import annotations

from .. import SourceNote

BUILTIN = ("apple", "files", "notion")


def get(name: str):
    """The adapter module named `name`, or a ValueError naming what exists.

    Imported on demand: the Apple adapter reaches for a macOS database and the
    Notion one for `zipfile`, and neither should cost anything on a machine
    running the other.
    """
    if name in BUILTIN:
        from importlib import import_module
        return import_module(f"{__name__}.{name}")
    from ... import plugins
    extra = getattr(plugins, "note_sources", lambda: {})()
    if name in extra:
        return extra[name]
    known = ", ".join(sorted(set(BUILTIN) | set(extra)))
    raise ValueError(f"unknown note source {name!r} — have: {known}")


def names() -> list[str]:
    from ... import plugins
    extra = getattr(plugins, "note_sources", lambda: {})()
    return sorted(set(BUILTIN) | set(extra))


__all__ = ["SourceNote", "BUILTIN", "get", "names"]
