"""A row wider than D1's statement cap still loads, whole, under its digest.

The 2026-11-25 nightly failed `Load D1` with SQLITE_TOOBIG: byte-batching puts
an oversized row in a statement of its own, which is still too long. Such a row
is now an INSERT of its first chunk plus one UPDATE per further chunk."""
import sqlite3

import duckdb

from exo.scripts_impl import publish_cf

D1_CAP = 100_000


def _emit(tmp_path, rows):
    con = duckdb.connect()
    con.execute("CREATE TABLE t (id VARCHAR, body VARCHAR, n INTEGER)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
    pq = tmp_path / "t1_notes.parquet"
    con.execute(f"COPY t TO '{pq}' (FORMAT parquet)")
    ddl, meta = publish_cf._emit_table(con, pq, "t1_notes", tmp_path)
    return ddl, meta, (tmp_path / "t1_notes.sql").read_text(encoding="utf-8")


def _statements(sql):
    # Every statement the emitter writes ends a line with ';'.
    out, cur = [], []
    for line in sql.splitlines():
        cur.append(line)
        if line.endswith(";"):
            out.append("\n".join(cur)); cur = []
    return out


def test_an_oversized_row_loads_whole_and_keeps_its_digest(tmp_path):
    big = ("a long Notion page, with an apostrophe's worth of quoting — é ") * 4_000
    rows = [("small", "short", 1), ("big", big, 2), ("big", big, 2), ("after", "x", 3)]
    ddl, meta, sql = _emit(tmp_path, rows)

    stmts = _statements(sql)
    assert max(len(s.encode()) for s in stmts) < D1_CAP
    assert any(s.startswith("UPDATE") for s in stmts)

    db = sqlite3.connect(":memory:")
    db.executescript(ddl)
    db.executescript(sql)
    got = db.execute("SELECT id, body FROM t1_notes ORDER BY rowid").fetchall()
    assert [r[0] for r in got] == ["small", "big", "big", "after"]
    assert got[1][1] == big and got[2][1] == big, "duplicates must each end whole"
    assert db.execute("SELECT sum(row_hash) FROM t1_notes").fetchone()[0] == meta["digest"]
    assert meta["rows"] == 4


def test_ordinary_rows_are_unchanged(tmp_path):
    _, _, sql = _emit(tmp_path, [("a", "b", 1), ("c", "d", 2)])
    assert "UPDATE" not in sql
