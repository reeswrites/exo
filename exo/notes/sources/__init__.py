"""The adapter registry — one entry per place notes come out of.

An adapter is a module with two names:

    LANDING   the directory under `notes/raw/` its notes land in. Its own, so a
              new source cannot inherit an existing zone's publication decision
              (see `exo.notes`).
    read(src[, seen]) -> list[SourceNote]

`src` is whatever `--from` gave, or None. `seen` — optional, and the local
adapters do not take it — is what is already landed, keyed by `external_id`, so
a source reached over a network can skip fetching a page it can already tell is
unchanged. It is an optimisation and never a correctness mechanism: ignoring it
is always right, and an adapter that uses it still has to hand every note back,
because an omitted note is absent rather than unchanged.

That is the whole interface. Nothing an adapter does can reach past it: it
cannot choose an id scheme, a filename, a frontmatter field or a landing path,
because those are the contract and the contract has one implementation.

## Which adapters ship in the engine

The rule is CONTRIBUTING's: a loader ships here if its input is a **format**,
and in an instance's `plugins/` if its input is a **place**. All three are things a stranger could
hold: a local Notes database, a Notion account, a directory of text files. That
is the test — not whether a source needs a credential, which Trakt, Raindrop and
the collections fetch all do while shipping here. An adapter for *your* team's
wiki, against *your* SSO, is yours.

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
