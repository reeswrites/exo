"""`wh publish --cf` — reshape the serve projection into a Cloudflare bundle.

The canonical projection (`zones/_serve/*.parquet`) is the decision about what
may leave; this is only a change of shape for the host that serves it. Nothing
here re-decides policy — it reads what publish already filtered, so a held row
cannot reappear by taking a different road out.

Cloudflare has no DuckDB, so the projection splits along its two access patterns:

  tabular  -> D1 (SQLite). 53k rows, ~5MB. Everything the fixed tools filter and
              aggregate over.
  vectors  -> a flat Float32Array in R2, loaded whole into the Worker isolate.
              10,971 x 384d x 4B = 16.85MB, comfortably inside the 128MB limit,
              so brute-force dot product replaces a vector database entirely.
              This is deliberate: Vectorize's free tier is ~5M stored dimensions
              and the corpus already sits at 4.21M, so it would be outgrown
              within months of normal note-writing.

The stored vectors are already unit-norm (verified: min = max = 1.0), so cosine
similarity IS the dot product. The Worker must normalize the *query* embedding
it gets back from Workers AI, and nothing else.

Bundle layout under zones/_serve/cf/:
    schema.sql        CREATE TABLE + indexes
    data/<table>.sql  batched INSERTs, one file per table
    vectors.f32       row-major float32, atom vectors then note vectors
    vectors.json      index sidecar — row i of the blob is rows[i]
    MANIFEST.json     counts, dim, byte sizes
"""
from __future__ import annotations

import json
import shutil
import sys
from array import array

import duckdb

from .. import config

# parquet type -> SQLite affinity. The projection only ever contains these.
_TYPE_MAP = {
    "VARCHAR": "TEXT",
    "BIGINT": "INTEGER",
    "INTEGER": "INTEGER",
    "BOOLEAN": "INTEGER",   # SQLite has no bool; 0/1
    "DOUBLE": "REAL",
    "FLOAT": "REAL",
}

# Indexes matching how the fixed tool set actually queries.
_INDEXES = {
    "t1_notes": ["origin_ref", "folder"],
    "t2_atom": ["origin_ref"],
    "t0_music": ["artist"],
    "t1_open_thread": [],
    # Every project tool filters or groups by repo first, and the commit table is
    # the only one big enough for that to matter.
    "t1_project": ["slug", "status"],
    "t1_project_commit": ["repo"],
    "t1_project_doc": ["repo"],
    "t1_project_open": ["repo"],
}

# D1 rejects an overlong statement with SQLITE_TOOBIG, and row width varies by
# three orders of magnitude across these tables (a scrobble vs a note body), so
# batching by ROW COUNT overflows on the wide ones. Batch by bytes instead.
_MAX_STATEMENT_BYTES = 40_000


def _sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _emit_table(con, parquet, table, out_dir) -> tuple[str, int]:
    cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()
    names = [c[0] for c in cols]
    ddl_cols = ", ".join(
        f'"{n}" {_TYPE_MAP.get(t.upper(), "TEXT")}' for n, t, *_ in cols
    )
    # DROP then CREATE, not CREATE IF NOT EXISTS. The bundle is authoritative
    # over D1 (ADR-0005), and that has to cover shape as well as rows: with IF
    # NOT EXISTS an existing table keeps its old columns forever, so adding
    # first_seen to T0 made every insert fail with "no column named first_seen"
    # against a database that reported itself healthy. Dropping costs nothing —
    # each data file re-inserts the table in full.
    ddl = [f'DROP TABLE IF EXISTS "{table}";', f'CREATE TABLE "{table}" ({ddl_cols});']
    for c in _INDEXES.get(table, []):
        if c in names:
            ddl.append(
                f'CREATE INDEX IF NOT EXISTS "idx_{table}_{c}" ON "{table}" ("{c}");'
            )

    rows = con.execute(f"SELECT * FROM read_parquet('{parquet}')").fetchall()
    collist = ", ".join(f'"{n}"' for n in names)
    header = f'INSERT INTO "{table}" ({collist}) VALUES\n'
    lines = [f'DELETE FROM "{table}";']  # re-import is idempotent

    batch: list[str] = []
    size = len(header)
    for r in rows:
        tup = "(" + ", ".join(_sql_literal(v) for v in r) + ")"
        # +2 for the ",\n" joiner; flush before crossing the limit, and never
        # emit an empty batch when a single row is itself oversized.
        if batch and size + len(tup) + 2 > _MAX_STATEMENT_BYTES:
            lines.append(header + ",\n".join(batch) + ";")
            batch, size = [], len(header)
        batch.append(tup)
        size += len(tup) + 2
    if batch:
        lines.append(header + ",\n".join(batch) + ";")

    (out_dir / f"{table}.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(ddl), len(rows)


def _emit_vectors(con, out) -> dict:
    """One flat float32 blob plus a sidecar whose row i is blob row i."""
    blob = array("f")
    index: list[dict] = []
    dim = None

    for kind, parquet, join in (
        ("atom", config.SERVE / "t2_atom_vec.parquet", config.SERVE / "t2_atom.parquet"),
        ("note", config.SERVE / "t2_note_vec.parquet", config.SERVE / "t1_notes.parquet"),
        ("post", config.SERVE / "t2_post_vec.parquet", config.SERVE / "t1_post.parquet"),
    ):
        # A zone can be added to the manifest and reach D1 before its vectors
        # exist (t2 rebuilds on a different pass). Skip rather than die: a
        # missing vector table costs recall on one kind, a crash costs the
        # whole nightly.
        if not parquet.exists() or not join.exists():
            print(f"  R2  {kind}_vec: no parquet yet \u2014 skipping")
            continue
        # carry enough metadata to render a hit without a second round trip
        label = "text" if kind == "atom" else "title"
        # The join side must be deduped by id BEFORE joining. `t1_notes.id` is
        # not unique — the vault holds 742 versioned copies of one note and 732
        # of another, all sharing a frontmatter id — so a naive LEFT JOIN
        # amplifies 3,064 note vectors into 1,087,978 rows, each carrying 384
        # floats. any_value is safe here precisely because the colliding rows
        # are versions of the same note.
        n_in = con.execute(f"SELECT count(*) FROM read_parquet('{parquet}')").fetchone()[0]
        rows = con.execute(f"""
            WITH j AS (
                SELECT id,
                       any_value("{label}")   AS label,
                       any_value(origin_ref)  AS origin_ref
                FROM read_parquet('{join}')
                GROUP BY id
            )
            SELECT v.id, COALESCE(j.label, ''), COALESCE(j.origin_ref, ''), v.vec
            FROM read_parquet('{parquet}') v
            LEFT JOIN j ON j.id = v.id
        """).fetchall()
        if len(rows) != n_in:
            raise SystemExit(
                f"publish-cf: {kind} join changed row count {n_in} -> {len(rows)}. "
                "A non-unique join key would corrupt the vector blob; refusing."
            )
        for vid, label_val, ref, vec in rows:
            if dim is None:
                dim = len(vec)
            elif len(vec) != dim:
                raise SystemExit(f"publish-cf: ragged vector dim {len(vec)} != {dim}")
            blob.extend(vec)
            index.append({
                "kind": kind,
                "id": vid,
                "ref": ref,
                "label": (label_val or "")[:180],
            })

    if sys.byteorder != "little":
        blob.byteswap()  # the Worker reads little-endian Float32Array
    (out / "vectors.f32").write_bytes(blob.tobytes())
    (out / "vectors.json").write_text(
        json.dumps({"dim": dim, "count": len(index), "normalized": True, "rows": index}),
        encoding="utf-8",
    )
    return {"count": len(index), "dim": dim, "bytes": len(blob) * 4}



IMPORT_SH = r"""#!/bin/sh
# Load this bundle into D1, making the database MATCH it exactly.
#
# Usage: [WRANGLER="npx wrangler"] ./import.sh <d1-database-name>
#
# Reconciles first: any table in D1 that is not in served-tables.txt is dropped.
# That is what makes tightening the manifest actually tighten (ADR-0005) — a zone
# flipped to `hold` stops being emitted, and without this its table would sit in
# D1 with every row intact while the manifest claimed it held.
#
# D1 keeps its own bookkeeping in the same database; sqlite_*, _cf_*, d1_* are
# protected, as is wh_* (the worker's own audit log), or the first import would
# delete the only record of who called what.
set -eu
DB="${1:?usage: import.sh <d1-database-name>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WRANGLER="${WRANGLER:-wrangler}"

command -v ${WRANGLER%% *} >/dev/null 2>&1 || {
  echo "import.sh: '${WRANGLER%% *}' not found. Try: WRANGLER='npx wrangler' $0 $DB" >&2
  exit 127
}

echo "== reconciling $DB against served-tables.txt =="
# Materialise the table list FIRST. Piping the query straight into the loop hides
# its exit status behind the pipeline, so a failing reconcile would fall through
# to loading data into a database that was never reconciled.
LIVE="$(mktemp)"
ACTUAL=""; MISMATCH=""
trap 'rm -f "$LIVE" "$ACTUAL" "$MISMATCH"' EXIT
# Capture stdout AND stderr: wrangler reports some failures on stdout, so
# redirecting only stdout into $LIVE swallows the error message and leaves an
# exit code with no explanation.
if ! $WRANGLER d1 execute "$DB" --remote --json \
     --command "SELECT name FROM sqlite_master WHERE type='table'" > "$LIVE" 2>&1; then
  echo "import.sh: could not list tables in D1. wrangler said:" >&2
  cat "$LIVE" >&2
  exit 1
fi

sed -n 's/.*"name" *: *"\([^"]*\)".*/\1/p' "$LIVE" | while IFS= read -r t; do
  [ -n "$t" ] || continue
  case "$t" in
    sqlite_*|_cf_*|d1_*|wh_*) continue ;;
  esac
  if ! grep -qxF "$t" "$HERE/served-tables.txt"; then
    echo "  DROP $t  (not on the served list)"
    $WRANGLER d1 execute "$DB" --remote --command "DROP TABLE IF EXISTS \"$t\""
  fi
done

echo "== schema =="
$WRANGLER d1 execute "$DB" --remote --file "$HERE/schema.sql"

echo "== data =="
# Batched, not one call per table. Each `wrangler d1 execute` spawns node and
# pays a full round trip, so 18 tables meant 18 startups against remote D1 —
# minutes of the deploy were process launches. Files are concatenated up to a
# size budget, keeping whole tables together so a failure is still attributable.
BATCH="$(mktemp)"
: > "$BATCH"
BUDGET=4000000
# D1 runs a large --file import ASYNCHRONOUSLY and refuses a second one while the
# first is in flight ("Cannot start another import until that completes"). The
# first batched version submitted all of them back to back, so the early tables
# loaded and every later one was rejected — leaving production with 0 notes while
# every step reported success. Wait for the queue between batches.
#
# The wait is a shared wall-clock budget, not a per-batch retry count. A count
# punishes position: every batch waits out the import before it, so the last one
# queues behind all of them and is the first to run out of tries. t2_atom is
# always submitted last and emptied exactly that way — the load reported failure
# on the one table whose only fault was being at the back of the line.
#
# Shared rather than per-batch on purpose. Five batches each free to wait 15
# minutes can exceed the job's 30-minute timeout, and a timeout kills the runner
# with no error at all — trading a legible failure for a silent one. One budget
# for the whole load keeps the total bounded and lets a late batch spend whatever
# the earlier ones did not.
BUSY_BUDGET=${D1_BUSY_BUDGET:-1200}
DEADLINE=$(( $(date +%s) + BUSY_BUDGET ))
# One place that talks to D1, so the busy-queue handling and the budget are
# shared by the bulk load and by any repair that follows it.
apply_file() {
  waited=0
  until out=$($WRANGLER d1 execute "$DB" --remote --file "$1" 2>&1); do
    case "$out" in
      *"long-running import"*|*"Cannot start another import"*)
        now=$(date +%s)
        if [ "$now" -ge "$DEADLINE" ]; then
          echo "  FAILED: D1 still busy; the load spent its whole ${BUSY_BUDGET}s budget waiting" >&2
          echo "  Raise D1_BUSY_BUDGET, but check the job timeout covers it first." >&2
          echo "$out" | sed 's/^/      /' >&2
          exit 1
        fi
        waited=$((waited + 5))
        sleep 5
        ;;
      *)
        echo "$out" | sed 's/^/      /' >&2
        exit 1
        ;;
    esac
  done
  # Say what the queue cost. A load that succeeds after waiting 9 minutes and one
  # that succeeds instantly print the same line otherwise, so the run before the
  # one that finally fails looks exactly like a healthy one.
  # `if`, not `[ ... ] && echo`: under `set -e` a false test as the function's
  # last command makes the caller return non-zero and kills the import.
  if [ "$waited" -gt 0 ]; then
    echo "    (waited ${waited}s for the previous import to drain)"
  fi
}

flush() {
  [ -s "$BATCH" ] || return 0
  echo "  applying batch ($(wc -c < "$BATCH" | tr -d ' ') bytes)"
  apply_file "$BATCH"
  : > "$BATCH"
}
for f in "$HERE"/data/*.sql; do
  sz=$(wc -c < "$f" | tr -d " ")
  cur=$(wc -c < "$BATCH" | tr -d " ")
  if [ "$cur" -gt 0 ] && [ $((cur + sz)) -gt "$BUDGET" ]; then flush; fi
  cat "$f" >> "$BATCH"
done
flush
rm -f "$BATCH"

# == verify ==
# The load reporting success is not evidence that the rows arrived. On
# 2026-08-19 all five batches returned success, printed no error, and exited 0,
# while t0_chat_topic, t0_event and t0_film — three files that shared one batch —
# ended EMPTY in production. The next night's guard found them and repaired
# them, which means the surface served zero events for a day and every step was
# green the whole time.
#
# So the bundle ships the row count it expects for every table, and the import
# reads them back. This is deliberately a check on CONTENT rather than on exit
# status: whatever swallowed that batch, it did not announce itself, and the
# only durable defence against a silent write is to go and look.
echo "== verifying row counts =="
ACTUAL="$(mktemp)"
MISMATCH="$(mktemp)"
trap 'rm -f "$LIVE" "$ACTUAL" "$MISMATCH"' EXIT

read_counts() {
  # --command, NOT --file. `--file` is the bulk IMPORT path: it uploads the SQL
  # and returns an import summary ("Total queries executed", "Rows read"), never
  # the rows a SELECT produced. Verifying through it reads back nothing at all
  # and calls every table absent. That import path is also the one that lost a
  # batch in silence, which is the reason this check exists.
  if ! $WRANGLER d1 execute "$DB" --remote --json \
       --command "$(cat "$HERE/verify.sql")" > "$ACTUAL" 2>&1; then
    echo "import.sh: could not read row counts back. wrangler said:" >&2
    cat "$ACTUAL" >&2
    exit 1
  fi
}

find_mismatches() {
  : > "$MISMATCH"
  while IFS="|" read -r t want; do
    [ -n "$t" ] || continue
    got=$(sed -n "s/.*\"$t\" *: *\([0-9][0-9]*\).*/\1/p" "$ACTUAL" | head -1)
    [ -n "$got" ] || got="absent"
    if [ "$got" != "$want" ]; then
      echo "$t|$want|$got" >> "$MISMATCH"
    fi
  done < "$HERE/expected-counts.txt"
}

read_counts
find_mismatches

if [ -s "$MISMATCH" ]; then
  echo "  the load lied — these tables do not match the bundle:"
  while IFS="|" read -r t want got; do
    echo "    $t: expected $want, found $got"
  done < "$MISMATCH"
  # Reapply, then look again — up to three rounds, with a pause between.
  #
  # Two different failures reach this point and only one of them is a lost
  # write. D1 also reads back stale for a few seconds after a bulk load: on
  # 2026-08-19 six tables verified as EMPTY, the repair reapplied all six, the
  # immediate re-read still said empty, and a hand check minutes later found
  # five of them full the whole time. A single immediate re-read cannot tell
  # "the write vanished" from "the read has not caught up", so it must not be
  # the thing that decides. Looking again after a pause can.
  attempt=1
  while [ -s "$MISMATCH" ] && [ "$attempt" -le 3 ]; do
    echo "  reapplying them one file at a time (attempt $attempt of 3)"
    while IFS="|" read -r t want got; do
      [ -f "$HERE/data/$t.sql" ] || { echo "    $t: no data file in the bundle" >&2; exit 1; }
      echo "    reapplying data/$t.sql"
      apply_file "$HERE/data/$t.sql" < /dev/null
    done < "$MISMATCH"
    # Long enough to outlast the stale read, short enough that three rounds
    # still fit inside the job's timeout.
    sleep $(( attempt * 10 ))
    read_counts
    find_mismatches
    attempt=$(( attempt + 1 ))
  done

  if [ -s "$MISMATCH" ]; then
    echo "  STILL WRONG after three rounds — refusing to call this a load:" >&2
    while IFS="|" read -r t want got; do
      echo "    $t: expected $want, found $got" >&2
    done < "$MISMATCH"
    exit 1
  fi
  echo "  repaired; every table now matches the bundle"
else
  echo "  every table matches the bundle"
fi

echo "== done: $DB now matches this bundle =="
"""


# D1 keeps its own bookkeeping in the same database. Reconciliation drops
# anything not served, so these prefixes must be protected or the first import
# destroys the database it is loading into.
# `wh_` is worker-owned state (the wh_audit call log). It is not in the
# published set, so without this the first reconcile would delete the only
# record of who called what.
_PROTECTED_PREFIXES = ("sqlite_", "_cf_", "d1_", "wh_")


def _emit_reconcile(out, counts: dict[str, int]) -> None:
    """Make the bundle authoritative over D1, not merely additive.

    Row-level tightening already revokes: every data file opens with DELETE FROM,
    so holding a folder or a path zone removes those rows on the next import.
    Table-level tightening did not — a zone flipped to `hold` simply stopped being
    mentioned, and D1 kept the table and every row in it, quietly, while the
    manifest said held.

    So the bundle ships the served list and an importer that reconciles against
    it: anything in D1 that is not on the list is dropped, including tables left
    by an older manifest or created by hand. Fail-closed applied to deletion.
    """
    served_tables = sorted(counts)
    (out / "served-tables.txt").write_text("\n".join(served_tables) + "\n",
                                           encoding="utf-8")

    # What the import reads back after loading. Shipped as data rather than
    # derived in the shell, because the shell cannot know what the bundle meant
    # to contain — only what D1 happens to hold, which is the thing in question.
    (out / "expected-counts.txt").write_text(
        "".join(f"{t}|{counts[t]}\n" for t in served_tables), encoding="utf-8")

    # One query, one round trip: a single row whose COLUMN NAMES are the table
    # names, so the shell matches a count to its table without depending on
    # column order or on how wrangler formats its JSON.
    #
    # Scalar subqueries, not `UNION ALL`. The obvious compound SELECT is what
    # this was written as first, and D1 rejects it outright — "too many terms in
    # compound SELECT: SQLITE_ERROR" — at a limit far below SQLite's documented
    # 500. It passed against a stubbed wrangler and failed against the real one.
    (out / "verify.sql").write_text(
        "SELECT\n" + ",\n".join(
            f'  (SELECT count(*) FROM "{t}") AS "{t}"' for t in served_tables
        ) + ";\n", encoding="utf-8")

    script = IMPORT_SH
    sh = out / "import.sh"
    sh.write_text(script, encoding="utf-8")
    sh.chmod(0o755)


def run() -> int:
    src = config.SERVE
    if not (src / "t1_notes.parquet").exists():
        print("publish-cf: no serve projection — run `wh publish` first")
        return 1

    out = src / "cf"
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    con = duckdb.connect(":memory:")
    try:
        schema: list[str] = []
        counts: dict[str, int] = {}
        for p in sorted(src.glob("*.parquet")):
            if p.stem.endswith("_vec"):
                continue  # vectors do not go to D1
            ddl, n = _emit_table(con, p, p.stem, out / "data")
            schema.append(ddl)
            counts[p.stem] = n
            print(f"  D1  {p.stem:<18}{n:>8,} rows")

        (out / "schema.sql").write_text("\n".join(schema) + "\n", encoding="utf-8")

        # The brief rides along as an MCP resource — the one artifact a client
        # loads without being asked.
        src_brief = src / "brief.md"
        if src_brief.exists():
            (out / "brief.md").write_text(src_brief.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  R2  brief.md        {len(src_brief.read_bytes()):>8,} bytes")
        _emit_reconcile(out, counts)
        vinfo = _emit_vectors(con, out)
        print(f"  R2  vectors.f32     {vinfo['count']:>8,} x {vinfo['dim']}d "
              f"= {vinfo['bytes'] / 1e6:.2f} MB")

        (out / "MANIFEST.json").write_text(json.dumps({
            "d1_tables": counts,
            "d1_rows_total": sum(counts.values()),
            "vectors": vinfo,
            "cosine": "vectors are unit-norm; similarity = dot product",
            "reconciles": "import.sh drops any D1 table not in served-tables.txt",
        }, indent=2), encoding="utf-8")
        print(f"  bundle -> {out}")
        return 0
    finally:
        con.close()
