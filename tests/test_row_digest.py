"""The per-row digest every published table carries (ADR-0026, phase 1).

The load has always reported success without evidence: on 2026-08-19 five
batches returned green and three tables were empty. `expected-counts.txt` fixed
the half of that a COUNT can see. This is the other half — a table holding the
right number of the wrong rows satisfies a count exactly, and that is what a
batch loading a stale file looks like.

`row_hash` is also the mechanism phase 2 needs: the sum read back per table is
what will let a load skip a table it can prove is already correct. It is landed
here while the load is still a full rewrite, so it can be wrong without being
dangerous.
"""
from __future__ import annotations

import re

import duckdb
import pytest

from exo.scripts_impl import publish_cf


def _parquet(tmp_path, con, rows, cols=("id", "origin_ref", "title")):
    """A parquet with the given rows, shaped like a published zone."""
    values = ", ".join(
        "(" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in r) + ")"
        for r in rows
    )
    collist = ", ".join(cols)
    con.execute(f"CREATE OR REPLACE TABLE z AS SELECT * FROM (VALUES {values}) "
                f"AS v({collist})")
    out = tmp_path / "z.parquet"
    con.execute(f"COPY z TO '{out}' (FORMAT PARQUET)")
    return out


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


def _emit(tmp_path, con, rows, table="t0_thing", **kw):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    pq = _parquet(tmp_path, con, rows, **kw)
    ddl, info = publish_cf._emit_table(con, pq, table, data)
    return ddl, info, (data / f"{table}.sql").read_text(encoding="utf-8")


def test_every_table_carries_the_digest_column(tmp_path, con):
    ddl, _, sql = _emit(tmp_path, con, [("a", "ref/1", "one")])
    assert '"row_hash" INTEGER' in ddl
    assert '"title", "row_hash"' in sql, (
        "the digest has to be in the INSERT column list too, or the column "
        "exists and is NULL on every row")


def test_the_digest_is_the_sum_of_what_was_written(tmp_path, con):
    """The bundle's number and the rows it shipped must agree, or the read-back
    compares a claim against a different claim."""
    rows = [("a", "ref/1", "one"), ("b", "ref/2", "two"), ("c", "ref/3", "three")]
    _, info, sql = _emit(tmp_path, con, rows)
    written = [int(h) for h in re.findall(r", (\d+)\)[,;]$", sql, re.M)]
    assert len(written) == 3
    assert info["digest"] == sum(written)
    assert info["rows"] == 3


def test_the_same_rows_digest_the_same_way_twice(tmp_path, con):
    """The property the whole design rests on, and the one Python's built-in
    `hash()` would quietly break: it is salted per process, so a digest built
    with it would differ between the run that wrote D1 and the run that reads
    it back."""
    rows = [("a", "ref/1", "one"), ("b", "ref/2", "two")]
    _, first, _ = _emit(tmp_path, con, rows)
    _, second, _ = _emit(tmp_path, con, rows)
    assert first["digest"] == second["digest"]


def test_a_changed_value_changes_the_digest(tmp_path, con):
    _, before, _ = _emit(tmp_path, con, [("a", "ref/1", "one")])
    _, after, _ = _emit(tmp_path, con, [("a", "ref/1", "ONE")])
    assert before["digest"] != after["digest"], (
        "same row count, different content — this is exactly the case the "
        "count cannot see and the digest exists for")


def test_the_row_count_alone_cannot_tell_two_tables_apart(tmp_path, con):
    """Stated as its own test because it is the failure mode, not a detail."""
    _, before, _ = _emit(tmp_path, con, [("a", "ref/1", "one"), ("b", "ref/2", "two")])
    _, after, _ = _emit(tmp_path, con, [("a", "ref/1", "one"), ("b", "ref/2", "TWO")])
    assert before["rows"] == after["rows"]
    assert before["digest"] != after["digest"]


# ───────────────────────── the key, recorded not enforced ─────────────────────────


def test_the_key_is_id_and_origin_ref_when_both_are_there(tmp_path, con):
    _, info, _ = _emit(tmp_path, con, [("a", "ref/1", "one")])
    assert info["key"] == ["id", "origin_ref"]
    assert info["duplicate_keys"] == 0


def test_rows_sharing_a_key_are_counted_and_not_fatal(tmp_path, con):
    """ADR-0026 §1 keys a diff on (id, origin_ref) and says to CHECK rather than
    assume. Two identical notes hash to one id (`Row.__post_init__` hashes the
    sorted payload values), and publish.py records 3,444 rows against 1,984
    distinct ids for the whole record. Whether the SERVED slice carries a
    collision is a question about production data, so this counts and reports
    instead of refusing — nothing keys off it yet."""
    rows = [("a", "ref/1", "one"), ("a", "ref/1", "one"), ("b", "ref/2", "two")]
    _, info, _ = _emit(tmp_path, con, rows)
    assert info["rows"] == 3
    assert info["duplicate_keys"] == 1


def test_the_same_id_under_different_refs_is_not_a_duplicate(tmp_path, con):
    """origin_ref is in the key for this reason: identical note bodies in two
    files are two rows, and publish already treats origin_ref as the join key
    throughout because t1_notes.id is not unique."""
    rows = [("a", "ref/1", "one"), ("a", "ref/2", "one")]
    _, info, _ = _emit(tmp_path, con, rows)
    assert info["duplicate_keys"] == 0


def test_a_zone_that_already_has_a_row_hash_column_is_refused(tmp_path, con):
    """Two columns of one name would make the CREATE fail at import time, in
    production, after the DROP. Better to fail here."""
    with pytest.raises(SystemExit, match="row_hash"):
        _emit(tmp_path, con, [("a", "1")], cols=("id", "row_hash"))


# ───────────────── end to end, against a real SQLite ─────────────────


def test_the_bundle_verifies_against_the_database_it_describes(tmp_path, con):
    """Load the bundle into real SQLite and run its own verify.sql against it.

    D1 is SQLite, and the two things this proves are the two a stubbed wrangler
    cannot: that `count(*) || '|' || sum(row_hash)` is valid SQL that returns
    what the shell expects to parse, and that the digest the bundle CLAIMS is
    the digest the loaded rows PRODUCE. Both have a precedent — `UNION ALL` over
    24 tables passed the stub and was rejected outright by D1.
    """
    import sqlite3

    data = tmp_path / "data"
    data.mkdir()
    tables = {}
    schema = []
    for name, rows in {
        "t0_thing": [("a", "ref/1", "one"), ("b", "ref/2", "two")],
        "t1_other": [("c", "ref/3", "three")],
        "t2_empty": [],
    }.items():
        if rows:
            pq = _parquet(tmp_path, con, rows)
        else:
            # An empty zone still ships a table; COALESCE is why its digest is 0
            # and not NULL, which would compare unequal to everything forever.
            con.execute("CREATE OR REPLACE TABLE z AS "
                        "SELECT * FROM (VALUES ('x','y','z')) AS v(id, origin_ref, title) "
                        "WHERE false")
            pq = tmp_path / "z.parquet"
            con.execute(f"COPY z TO '{pq}' (FORMAT PARQUET)")
        ddl, info = publish_cf._emit_table(con, pq, name, data)
        schema.append(ddl)
        tables[name] = info
    publish_cf._emit_reconcile(tmp_path, tables, "full")

    db = sqlite3.connect(":memory:")
    db.executescript("\n".join(schema))
    for name in tables:
        db.executescript((data / f"{name}.sql").read_text(encoding="utf-8"))

    cur = db.execute((tmp_path / "verify.sql").read_text(encoding="utf-8"))
    live = dict(zip([d[0] for d in cur.description], cur.fetchone()))

    expected = dict(
        line.split("|", 1)
        for line in (tmp_path / "expected-counts.txt").read_text().splitlines()
    )
    assert live == expected, (
        "the bundle's claim and the database built from it disagree:\n"
        f"  live:     {live}\n  expected: {expected}")
    assert live["t2_empty"] == "0|0", "an empty table must read 0|0, never a NULL"
