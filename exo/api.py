"""Read API — what skills and engines call. Two doors, one wall.

    read(sql)          skills / display  — sees ALL zones (t0+t1+t2), all rows
    read_source(sql)   derivation engines — sees ONLY t0+t1, and only grounds=true rows

A derivation engine that tries to `SELECT * FROM t2_atoms` through
`read_source` gets a "table does not exist" error — because on the source
profile that view was never created. And a grounds=false row in a t0/t1 zone
(a saved link, a chat turn) is filtered out of the source view, so it can't be
read as source either. That is the anti-collapse guarantee made mechanical: T2
— and anything else that doesn't ground — can never re-enter derivation.
"""
from __future__ import annotations

from typing import Any

from . import catalog


def read(sql: str, params: list[Any] | None = None) -> list[tuple]:
    """Query across every zone. For skills, recaps, muse, the collider's display.

    Read-only connection: concurrent-safe (many readers, so parallel `wh` calls don't
    collide on the catalog's file lock) and it can't mutate the store."""
    con = catalog.connect("full", read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def read_source(sql: str, params: list[Any] | None = None) -> list[tuple]:
    """Query the grounding tiers only (t0+t1). For atomizer/bonder/vectorizer.

    Cannot see t2_* — the wall. If your derivation needs a t2 view, that is the
    smell the invariant is designed to catch.
    """
    con = catalog.connect("source")
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def views(profile: str = "full") -> list[str]:
    con = catalog.connect(profile)
    try:
        return [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type='VIEW' ORDER BY table_name"
        ).fetchall()]
    finally:
        con.close()
