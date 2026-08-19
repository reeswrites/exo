"""DuckDB catalog — the one SQL surface over tier-native parquet.

Views are named `<tier>_<name>` (t0_music, t1_notes, t2_atoms). Two profiles:

  full    every view — for skills / display / cross-zone joins
  source  ONLY t0_* and t1_* — for derivation engines

`source` is the wall (ADR-0001, Fig 4), and it has TWO halves:
  tier    — source registers only t0_/t1_ views; a t2_ view doesn't exist on that
            connection, so derivation structurally cannot SELECT it.
  grounds — each source view exposes only its grounds=true rows (`WHERE grounds`),
            so a NON-grounding zone (chat-logs, saved links) can live in a grounding
            tier and still be invisible to derivation.
The read-direction law is enforced by absence (of views AND of rows), not by convention.

The cache namespace (ADR-0002): `zones/_cache/*.parquet` register as `cache_<name>`
views in the `full` profile ONLY — never `source`. So machine cache output stays
invisible to the wall and cannot re-enter derivation, exactly like t2. Cache
views are joinable and disposable; they carry no rebuild contract and are
deliberately kept OUT of the `rows` envelope ledger (a cache is not a tier, and
its parquet need not carry the full provenance envelope).
"""
from __future__ import annotations

import glob
from pathlib import Path

import duckdb

from . import config

_TIER_DIR = {"t0": config.T0, "t1": config.T1, "t2": config.T2}


def _views(profile: str) -> list[tuple[str, str, Path]]:
    """(view_name, tier, parquet_path) for the requested profile."""
    tiers = ("t0", "t1") if profile == "source" else ("t0", "t1", "t2")
    out = []
    for tier in tiers:
        for p in sorted(glob.glob(str(_TIER_DIR[tier] / "*.parquet"))):
            name = Path(p).stem
            out.append((f"{tier}_{name}", tier, Path(p)))
    return out


def _create_view(con: duckdb.DuckDBPyConnection, view: str, path: Path,
                 grounds_only: bool = False) -> None:
    # path is repo-internal, not user input; inline with quote-escaping
    # (DuckDB won't accept a bound parameter inside CREATE VIEW).
    # grounds_only is the SECOND half of the wall (see _register): on the source
    # profile a tier view exposes only grounds=true rows, so a non-grounding zone
    # (chat, saved links) can live in t0/t1 yet stay invisible to derivation.
    lit = str(path).replace("'", "''")
    where = " WHERE grounds" if grounds_only else ""
    con.execute(f"CREATE OR REPLACE VIEW \"{view}\" AS SELECT * FROM read_parquet('{lit}'){where}")


def _register(con: duckdb.DuckDBPyConnection, profile: str) -> list[str]:
    names = []
    # The wall has two halves: TIER (source sees only t0_/t1_, never t2_ — enforced by
    # `_views`) and GROUNDS (source sees only grounds=true rows — enforced here). Together
    # they mean a non-grounding zone in a grounding tier still can't feed derivation.
    grounds_only = profile == "source"
    for view, _tier, path in _views(profile):
        _create_view(con, view, path, grounds_only=grounds_only)
        names.append(view)
    # a cross-zone provenance view: every row's envelope, tier-tagged. Built from
    # tier views only — cache views are excluded (see below).
    if names:
        parts = [
            f'SELECT id, tier, zone, source, author, created, grounds, regenerable FROM "{v}"'
            for v in names
        ]
        con.execute("CREATE OR REPLACE VIEW rows AS " + " UNION ALL ".join(parts))
    # Cache namespace (ADR-0002): full profile ONLY, and AFTER `rows` so cache
    # views never enter the envelope ledger. The `source` wall registers nothing
    # here, so cache output stays invisible to derivation.
    if profile == "full":
        for p in sorted(glob.glob(str(config.CACHE / "*.parquet"))):
            view = f"cache_{Path(p).stem}"
            _create_view(con, view, Path(p))
            names.append(view)
        # cache_open_questions is a VIEW over t1_open_thread, not a parquet (B1 /
        # 0004-vault-relocation §⚠️): open-questions is projected ONCE (t1_open_thread,
        # canonical). This re-exposes it under the old cache column names so consumers
        # (life-terminal openThreads, garden.links) keep working. Runs AFTER the glob so
        # CREATE OR REPLACE wins over any stale open_questions.parquet the glob registered.
        # Only when the zone it re-exposes actually exists. An instance that has
        # ingested nothing yet has no t1_open_thread, and an unconditional view
        # over a missing table makes the FIRST command a stranger runs after
        # `exo init` die inside DuckDB with "Table with name t1_open_thread does
        # not exist" — a catalog error standing in for "you have no data yet".
        # Existence is not enough: an empty zone writes a parquet holding only
        # the provenance envelope, so the view exists with none of the columns
        # this one selects. The error DuckDB raises then names a column rather
        # than the cause, and it comes out of `exo rebuild` — the first command
        # a new instance runs.
        oq_cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 't1_open_thread'").fetchall()}
        if {"state", "question", "note_source", "origin_ref"} <= oq_cols:
            con.execute(
                "CREATE OR REPLACE VIEW cache_open_questions AS "
                "SELECT id AS oq_id, state AS state, question AS question, "
                "note_source AS source, origin_ref AS path, created AS created "
                "FROM t1_open_thread"
            )
            if "cache_open_questions" not in names:
                names.append("cache_open_questions")
    return names


def build() -> list[str]:
    """(Re)build the persistent full catalog at config.CATALOG. Returns views."""
    config.ensure_dirs()
    con = duckdb.connect(str(config.CATALOG))
    try:
        # drop stale views first — a zone whose parquet was removed (e.g. the
        # retired t2_molecule) must not linger in the catalog.
        for (v,) in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
        ).fetchall():
            con.execute(f'DROP VIEW IF EXISTS "{v}"')
        return _register(con, "full")
    finally:
        con.close()


def connect(profile: str = "full", read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """A connection with views registered for the profile.

    full   -> persistent catalog file (shareable with external tools)
    source -> fresh in-memory con with only t0_/t1_ views (the wall)

    read_only (full only): open the catalog read-only and TRUST its persisted views
    instead of re-registering. DuckDB permits many concurrent read-only openers but only
    one read-write, so every READ path (queryjson, query, search) uses this — otherwise
    parallel `wh` calls (e.g. Life Terminal's launchpad firing four at once) collide on the
    file lock. Views are `SELECT * FROM read_parquet(path)`, so they reflect the live
    parquet even without a re-register; `build`/ingest keep them current.
    """
    if profile == "full":
        if read_only:
            return duckdb.connect(str(config.CATALOG), read_only=True)
        con = duckdb.connect(str(config.CATALOG))
        _register(con, "full")
        return con
    if profile == "source":
        con = duckdb.connect(":memory:")
        _register(con, "source")
        return con
    raise ValueError(f"unknown profile {profile!r}")
