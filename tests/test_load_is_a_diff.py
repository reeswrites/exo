"""The load skips the tables it can prove are already right (ADR-0026 phase 2).

A nightly used to rewrite the corpus to change about sixty rows: 128,275 rows
written against a free budget of 100,000 a day. Phase 2 is the cheap half of the
answer and it is a SKIP rather than a merge — a table is either rewritten exactly
as it always was, or not touched at all.

Everything here runs `import.sh` against a **real SQLite database**, through a
fake `wrangler` that executes the SQL instead of answering with canned JSON. That
is deliberate and the file it replaces says why: two bugs passed a stub and failed
against D1 (`UNION ALL` over 24 tables, and `--file` never returning SELECT rows).
A skip decided from a read that the stub invented would be a third.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess

import duckdb
import pytest

from exo.scripts_impl import publish_cf


# ───────────────────────── a wrangler backed by real SQLite ─────────────────────────

# `--json`, pretty-printed one key per line, because that is what wrangler emits
# and because import.sh parses both the table list and the verify result with a
# per-line sed whose leading `.*` is greedy. Collapsed onto one line, only the
# last key of each object would ever be seen.
_WRANGLER_PY = r'''
import json, os, sqlite3, sys

args = sys.argv[1:]
if log := os.environ.get("WRANGLER_LOG"):
    with open(log, "a") as fh:
        fh.write(" ".join(args) + "\n")

cmd = path = None
for i, a in enumerate(args):
    if a == "--command" and i + 1 < len(args):
        cmd = args[i + 1]
    if a == "--file" and i + 1 < len(args):
        path = args[i + 1]

con = sqlite3.connect(os.environ["WRANGLER_DB"])
try:
    if path is not None:
        sql = open(path).read()
        if seen := os.environ.get("WRANGLER_SQL"):
            with open(seen, "a") as fh:
                fh.write(sql)
        before = con.total_changes
        con.executescript(sql)
        con.commit()
        # total_changes counts INSERT/UPDATE/DELETE rows and not DDL, which is
        # the same distinction D1 bills on: dropping a table is not charged per
        # row it held. It does NOT count index entries, so this number is smaller
        # than D1's for the same load — the tests below compare it to zero and to
        # itself, never to a production figure.
        meta = {"rows_written": con.total_changes - before}
        # A write that goes in and then quietly does not stick — the 2026-08-19
        # failure, arranged on purpose so a test can see it happen BETWEEN the
        # probe and the read-back. Fires once: the file is removed as it runs.
        if (s := os.environ.get("WRANGLER_SABOTAGE")) and os.path.exists(s):
            con.executescript(open(s).read())
            con.commit()
            os.unlink(s)
        out = [{"results": [], "success": True, "meta": meta}]
    else:
        cur = con.execute(cmd)
        rows = ([dict(zip([d[0] for d in cur.description], r))
                 for r in cur.fetchall()] if cur.description else [])
        con.commit()
        out = [{"results": rows, "success": True, "meta": {"rows_written": 0}}]
except sqlite3.Error as e:
    print(f"wrangler: D1 rejected it: {e}", file=sys.stderr)
    sys.exit(1)
print(json.dumps(out, indent=2))
'''


@pytest.fixture
def d1(tmp_path):
    """A fake wrangler on PATH, a real SQLite file behind it, and the logs."""
    py = tmp_path / "wrangler.py"
    py.write_text(_WRANGLER_PY, encoding="utf-8")
    sh = tmp_path / "wrangler"
    sh.write_text(f'#!/bin/sh\nexec python3 "{py}" "$@"\n', encoding="utf-8")
    sh.chmod(0o755)
    return {
        "wrangler": sh,
        "db": tmp_path / "warehouse.sqlite",
        "log": tmp_path / "calls.log",
        "sql": tmp_path / "applied.sql",
        "sabotage": tmp_path / "sabotage.sql",
    }


def _import(bundle, d1, **env):
    e = {**os.environ,
         "WRANGLER": str(d1["wrangler"]),
         "WRANGLER_SABOTAGE": str(d1["sabotage"]),
         "WRANGLER_DB": str(d1["db"]),
         "WRANGLER_LOG": str(d1["log"]),
         "WRANGLER_SQL": str(d1["sql"]),
         "PATH": f"{d1['wrangler'].parent}:{os.environ['PATH']}",
         "D1_REPAIR_PAUSE": "1"}
    e.update(env)
    return subprocess.run(["sh", str(bundle / "import.sh"), "warehouse"],
                          capture_output=True, text=True, env=e, timeout=300)


def _written(proc):
    """The meter's own number, off the line the import prints."""
    for line in proc.stdout.splitlines():
        if "rows written" in line:
            return int(line.split("—")[1].split("rows written")[0].strip())
    raise AssertionError(f"no meter line in:\n{proc.stdout}")


# ───────────────────────── a bundle, from real parquet ─────────────────────────


ZONES = {
    "t0_music": [("a", "lastfm.csv", "Bowie"), ("b", "lastfm.csv", "Eno")],
    "t1_notes": [("n1", "notes/one.md", "a thought")],
    "t2_empty": [],
}


def _bundle(tmp_path, zones=None, scope="full", name="cf"):
    zones = ZONES if zones is None else zones
    out = tmp_path / name
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    (out / "ddl").mkdir()
    con = duckdb.connect(":memory:")
    schema, tables = [], {}
    try:
        for table, rows in zones.items():
            values = ", ".join(
                "(" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in r) + ")"
                for r in rows) or "('x','y','z')"
            where = "" if rows else " WHERE false"
            con.execute(f"CREATE OR REPLACE TABLE z AS SELECT * FROM (VALUES {values}) "
                        f"AS v(id, origin_ref, body){where}")
            pq = tmp_path / f"{table}.parquet"
            con.execute(f"COPY z TO '{pq}' (FORMAT PARQUET)")
            ddl, info = publish_cf._emit_table(con, pq, table, out / "data",
                                               ddl_dir=out / "ddl")
            schema.append(ddl)
            tables[table] = info
    finally:
        con.close()
    (out / "schema.sql").write_text("\n".join(schema) + "\n", encoding="utf-8")
    publish_cf._emit_reconcile(out, tables, scope)
    return out, tables


# ───────────────────────── the first load, and the second ─────────────────────────


def test_the_first_load_writes_everything_and_verifies(tmp_path, d1):
    bundle, _ = _bundle(tmp_path)
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # An empty database cannot answer verify.sql at all — a scalar subquery over
    # a table that does not exist fails the whole query — so the probe fails and
    # the run falls back to writing everything. That is the fail-closed default
    # and a fresh database is the ordinary case for it.
    assert "could not read the current state" in proc.stdout
    assert _written(proc) == 3, proc.stdout
    assert "every table matches the bundle" in proc.stdout
    live = sqlite3.connect(d1["db"])
    assert live.execute('SELECT count(*) FROM "t0_music"').fetchone()[0] == 2


def test_the_same_bundle_twice_writes_nothing_the_second_time(tmp_path, d1):
    """The whole point of phase 2, measured rather than asserted."""
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()

    again = _import(bundle, d1)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "2 table(s) already match this bundle" in again.stdout
    # t2_empty is the one that still reloads, and its reload writes nothing.
    assert _written(again) == 0, again.stdout
    assert "every table matches the bundle" in again.stdout
    applied = d1["sql"].read_text() if d1["sql"].exists() else ""
    assert 'DROP TABLE IF EXISTS "t0_music"' not in applied, (
        "a table that matched must not be dropped and re-inserted; the drop is "
        "free at the meter but the re-insert is the entire bill")
    assert 'INSERT INTO "t0_music"' not in applied


def test_only_the_table_that_changed_is_rewritten(tmp_path, d1):
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()

    moved = dict(ZONES)
    moved["t1_notes"] = [("n1", "notes/one.md", "a thought"),
                         ("n2", "notes/two.md", "another")]
    bundle, _ = _bundle(tmp_path, moved)
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reload t1_notes  (has 1 rows, bundle has 2)" in proc.stdout, proc.stdout
    applied = d1["sql"].read_text()
    assert 'INSERT INTO "t1_notes"' in applied
    assert 'INSERT INTO "t0_music"' not in applied
    live = sqlite3.connect(d1["db"])
    assert live.execute('SELECT count(*) FROM "t1_notes"').fetchone()[0] == 2


def test_a_changed_row_is_caught_when_the_count_does_not_move(tmp_path, d1):
    """The count sees nothing here. The digest is the only thing that can."""
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()

    edited = dict(ZONES)
    edited["t1_notes"] = [("n1", "notes/one.md", "a DIFFERENT thought")]
    bundle, _ = _bundle(tmp_path, edited)
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reload t1_notes  (same row count, different content)" in proc.stdout
    live = sqlite3.connect(d1["db"])
    assert live.execute('SELECT "body" FROM "t1_notes"').fetchone()[0] \
        == "a DIFFERENT thought"


def test_the_meter_sums_across_batches(tmp_path, d1):
    """The batching is by BYTES, and a load big enough to split is the ordinary
    case in production — so the total has to be a sum and not the last call's
    number. `t0_music` here is padded past the 4MB batch budget on purpose."""
    wide = {"t0_music": [(f"id{i}", "lastfm.csv", "x" * 2200) for i in range(2000)],
            "t1_notes": ZONES["t1_notes"]}
    bundle, _ = _bundle(tmp_path, wide)
    assert (bundle / "data" / "t0_music.sql").stat().st_size > 4_000_000
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("applying batch") >= 2, (
        f"the load has to split to test the sum:\n{proc.stdout}")
    assert _written(proc) == 2001, proc.stdout
    # Whole tables stay together in a batch so a failure is still attributable.
    applied = d1["sql"].read_text()
    assert applied.index('CREATE TABLE "t1_notes"') > applied.index(
        'INSERT INTO "t0_music"')


# ───────────────── the two things a sum over rows cannot see ─────────────────


def test_an_index_change_still_reaches_a_table_that_would_otherwise_skip(
        tmp_path, d1, monkeypatch):
    """The case that made the shape part of the row hash.

    An index is not a row, so `sum(row_hash)` cannot see one appear or vanish —
    and a table whose digest matches is never dropped, so its indexes are never
    re-created. a74d25d is exactly this change: removing the `t0_music` artist
    index is what bought the headroom phase 2 spends, and on a skipped table it
    would have been a commit that changed nothing in production, silently.
    """
    monkeypatch.setitem(publish_cf._INDEXES, "t0_music", ["origin_ref"])
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    live = sqlite3.connect(d1["db"])
    assert live.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='index' "
        "AND name='idx_t0_music_origin_ref'").fetchone()[0] == 1
    live.close()
    d1["sql"].unlink()

    # Same rows, same count. Only the index list moves.
    monkeypatch.setitem(publish_cf._INDEXES, "t0_music", [])
    bundle, _ = _bundle(tmp_path)
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reload t0_music" in proc.stdout, (
        "the index list changed and the rows did not; without the shape in the "
        f"row hash this table skips and keeps the old index forever.\n{proc.stdout}")
    live = sqlite3.connect(d1["db"])
    assert live.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='index' "
        "AND name='idx_t0_music_origin_ref'").fetchone()[0] == 0


def test_an_empty_table_is_never_skipped(tmp_path, d1):
    """The other blind spot, and the reason it is handled in the shell instead.

    An empty table's digest is 0 whatever columns it has, so folding the shape
    into the row hash cannot help — there are no rows to carry it. So the import
    reloads any table the bundle says is empty, which costs nothing: re-creating
    an empty table writes no rows at all.
    """
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()
    again = _import(bundle, d1)
    applied = d1["sql"].read_text()
    assert 'DROP TABLE IF EXISTS "t2_empty"' in applied, applied
    assert _written(again) == 0, "and it still costs nothing"


def test_a_column_change_on_an_empty_table_reaches_production(tmp_path, d1):
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0

    # Same zone, still empty, one more column — the `first_seen` scar's shape.
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE z AS SELECT * FROM (VALUES ('x','y','z','w')) "
                "AS v(id, origin_ref, body, first_seen) WHERE false")
    pq = tmp_path / "t2_empty.parquet"
    con.execute(f"COPY z TO '{pq}' (FORMAT PARQUET)")
    ddl, info = publish_cf._emit_table(con, pq, "t2_empty", bundle / "data",
                                       ddl_dir=bundle / "ddl")
    con.close()
    _, tables = _bundle(tmp_path, name="cf2")
    tables["t2_empty"] = info
    publish_cf._emit_reconcile(bundle, tables, "full")

    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    live = sqlite3.connect(d1["db"])
    cols = [r[1] for r in live.execute('PRAGMA table_info("t2_empty")')]
    assert "first_seen" in cols, (
        "an empty table that never reloads keeps its old columns, and the worker "
        "answers `no such column` against a bundle that reported success")


# ───────────────────────── the escape hatches ─────────────────────────


def test_exo_d1_load_full_rewrites_everything(tmp_path, d1):
    """The rollback: no revert, no deploy, one environment variable."""
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()

    proc = _import(bundle, d1, EXO_D1_LOAD="full")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "rewriting every table without asking" in proc.stdout
    assert _written(proc) == 3, proc.stdout
    applied = d1["sql"].read_text()
    assert 'INSERT INTO "t0_music"' in applied


def test_an_unrecognised_load_mode_refuses_rather_than_guessing(tmp_path, d1):
    bundle, _ = _bundle(tmp_path)
    proc = _import(bundle, d1, EXO_D1_LOAD="incremental")
    assert proc.returncode == 1
    assert "neither 'diff' nor 'full'" in (proc.stdout + proc.stderr)
    assert not d1["db"].exists(), "it must refuse before it writes"


def test_an_unreadable_base_reloads_everything(tmp_path, d1):
    """Fail closed. An unanswerable probe is not a matching base."""
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()
    # A table vanishing under the bundle is what a hand-DROP or a half-finished
    # earlier run looks like. verify.sql names every served table, so the whole
    # probe fails rather than one entry going missing.
    live = sqlite3.connect(d1["db"])
    live.executescript('DROP TABLE "t1_notes";')
    live.commit()
    live.close()

    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "could not read the current state" in proc.stdout
    applied = d1["sql"].read_text()
    for t in ZONES:
        assert f'INSERT INTO "{t}"' in applied or not ZONES[t]


# ───────────────── the guarantees phase 2 was not allowed to move ─────────────────


def test_a_revoked_row_still_leaves_a_table_that_would_otherwise_skip(tmp_path, d1):
    """The privacy property, stated as its own test.

    Before phase 2 a held row was gone by demolition: the table was dropped and
    only served rows came back. Now the table might be skipped — so the thing
    that has to be true is that holding a row CHANGES ITS TABLE'S DIGEST, which
    means the table stops matching and is rewritten without it.
    """
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0

    held = dict(ZONES)
    held["t0_music"] = [("a", "lastfm.csv", "Bowie")]  # Eno is held
    bundle, _ = _bundle(tmp_path, held)
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    live = sqlite3.connect(d1["db"])
    assert [r[0] for r in live.execute('SELECT "body" FROM "t0_music"')] == ["Bowie"]


def test_a_revoked_table_is_still_dropped_by_the_reconcile(tmp_path, d1):
    """Table-level revocation is the reconcile's job and phase 2 does not touch
    it — but the reconcile now runs AFTER the probe, so prove it still runs."""
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0

    smaller = {k: v for k, v in ZONES.items() if k != "t0_music"}
    bundle, _ = _bundle(tmp_path, smaller)
    proc = _import(bundle, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    live = sqlite3.connect(d1["db"])
    assert live.execute("SELECT count(*) FROM sqlite_master WHERE name='t0_music'"
                        ).fetchone()[0] == 0, (
        "a zone flipped to `hold` must lose its table, probe or no probe")


def test_a_partial_bundle_takes_the_same_path_and_drops_nothing(tmp_path, d1):
    """Phase 4, for free. A notes-lane fire costs ~42,000 rows written today
    because it rewrites four tables in full; it should cost nothing when the
    note that triggered it is the only thing that moved."""
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sql"].unlink()

    lane, _ = _bundle(tmp_path, {"t1_notes": ZONES["t1_notes"]},
                      scope="partial", name="lane")
    proc = _import(lane, d1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dropping nothing" in proc.stdout
    assert "1 table(s) already match this bundle" in proc.stdout
    assert _written(proc) == 0, proc.stdout
    live = sqlite3.connect(d1["db"])
    assert live.execute("SELECT count(*) FROM sqlite_master WHERE name='t0_music'"
                        ).fetchone()[0] == 1, (
        "a partial bundle knows nothing about the other zones and must not "
        "reconcile against its own list")


def test_the_verify_step_still_covers_the_tables_that_were_skipped(tmp_path, d1):
    """A skipped table is a claim that D1 already holds the right rows. The
    read-back after the load is what turns that claim back into evidence, so it
    has to keep asking about every served table and not only the rewritten ones.

    Arranged as the real failure is arranged: t0_music matches when the probe
    reads it, the load skips it, and it loses a row while the load is in flight.
    That is 2026-08-19 — five green batches and three empty tables — happening to
    a table this run never wrote. The probe cannot see it; the read-back must.
    """
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    d1["sabotage"].write_text('DELETE FROM "t0_music" WHERE "id" = \'a\';\n',
                              encoding="utf-8")

    proc = _import(bundle, d1)
    assert "the load lied" in proc.stdout, proc.stdout
    assert "t0_music: expected 2 rows, found 1 rows" in proc.stdout
    # Caught AND repaired, so the run is green — which is the right outcome and
    # the reason the read-back was worth keeping. The repair re-creates a skipped
    # table from the bundle's own DDL as well as its data: this run never wrote
    # the table, so there is no earlier state of it to patch on top of.
    assert "reapplying ddl/t0_music.sql" in proc.stdout, proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr
    live = sqlite3.connect(d1["db"])
    assert live.execute('SELECT count(*) FROM "t0_music"').fetchone()[0] == 2


def test_the_digest_proves_what_the_load_wrote_not_that_nobody_edited_d1(tmp_path, d1):
    """An honest limit, recorded so it is not mistaken for a guarantee.

    `row_hash` is a stored column, so a hand-edit that changes `body` and leaves
    the hash alone is invisible to `sum(row_hash)`. That is acceptable, and it is
    acceptable for a stated reason rather than by oversight: the only other writer
    to this database is the worker, and the worker writes `wh_*` and nothing else.
    The digest answers "are the rows this bundle wrote the rows that are here",
    which is the question the load can be wrong about.
    """
    bundle, _ = _bundle(tmp_path)
    assert _import(bundle, d1).returncode == 0
    live = sqlite3.connect(d1["db"])
    live.executescript('UPDATE "t0_music" SET "body" = \'forged\' WHERE "id" = \'a\';')
    live.commit()
    live.close()

    proc = _import(bundle, d1)
    assert proc.returncode == 0
    assert "already match this bundle" in proc.stdout


def test_a_bundle_missing_a_ddl_file_refuses_rather_than_loading_half(tmp_path, d1):
    bundle, _ = _bundle(tmp_path)
    (bundle / "ddl" / "t0_music.sql").unlink()
    proc = _import(bundle, d1)
    assert proc.returncode == 1
    assert "broken bundle" in (proc.stdout + proc.stderr)


# ───────────────────────── what the bundle now ships ─────────────────────────


def test_the_bundle_ships_one_ddl_file_per_table_and_the_whole_schema_too(tmp_path):
    bundle, tables = _bundle(tmp_path)
    for t in tables:
        assert (bundle / "ddl" / f"{t}.sql").exists()
        assert f'CREATE TABLE "{t}"' in (bundle / "ddl" / f"{t}.sql").read_text()
    # schema.sql is what the worker's test harnesses load (worker/test/harness.mjs
    # and sqlcheck.mjs both read it), so it stays whole even though import.sh
    # stopped reading it.
    whole = (bundle / "schema.sql").read_text()
    for t in tables:
        assert f'CREATE TABLE "{t}"' in whole


def test_the_manifest_records_each_tables_shape(tmp_path):
    _, tables = _bundle(tmp_path)
    assert len({i["shape"] for i in tables.values()}) == len(tables), (
        "three different shapes must fingerprint three different ways")
    assert all(len(i["shape"]) == 12 for i in tables.values())
