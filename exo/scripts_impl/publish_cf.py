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
    exposure.json     publicity per served zone (ADR-0019), resolved by `publish`
    surface.json      the tools this instance offers (ADR-0020), likewise resolved
    MANIFEST.json     counts, dim, byte sizes
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from array import array

import duckdb

from .. import config

# The per-row digest column. Every published table carries it, and the import
# reads its sum back to decide whether a table needs writing at all (ADR-0026).
# Costs nothing at the meter: a column is not an index, and D1 bills index
# entries, not columns.
ROW_HASH = "row_hash"

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
    # No index, deliberately. Seven query sites read t0_music and not one can
    # do a LOOKUP through an index on `artist`: `music` wraps it in lower() and
    # matches with a leading %, `releases` and `collection` group on
    # lower(artist), `around_the_time` and the relevance window filter on
    # `created` first, and `consumption` and `medium` never name the column. At
    # most it lets a GROUP BY read in index order instead of sorting — inside a
    # scan of all 41,294 rows that those queries do anyway, and which `music`
    # then re-sorts by plays.
    #
    # It is not free. D1 bills a row written per index entry, so this one costs
    # 41,294 writes on every full import — a third of the whole daily free
    # budget, spent on a sort. See ADR-0026, which is about the rewrite itself.
    "t0_music": [],
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


# Characters that make a statement unloadable rather than merely ugly. A NUL
# inside a string literal is the one that matters: D1 accepted such a batch,
# reported success, and left the table empty — the publish looked green and the
# surface served nothing. Doubling the quote handles apostrophes; nothing
# handles a NUL, so it must not get this far.
_UNLOADABLE = {"\x00"}


class BinaryValue(ValueError):
    """A value that cannot survive the trip to D1, caught before it is written."""


def _sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    text = str(v)
    bad = _UNLOADABLE & set(text)
    if bad:
        # Loud, and with enough of the value to recognise. A NUL here means a
        # loader read something that is not text — an AppleDouble sidecar, a
        # binary file that matched a glob — and the honest response is to stop
        # rather than to publish a row that will vanish without a word.
        raise BinaryValue(
            f"value contains {len(bad)} unloadable byte(s) and would be dropped "
            f"by D1 in silence: {text[:80]!r}")
    return "'" + text.replace("'", "''") + "'"


def _row_hash(rendered: str) -> int:
    """A 31-bit digest of one row, from the literals about to be written.

    Hashing the RENDERED tuple rather than the parquet values is deliberate: it
    digests exactly what D1 will hold, so a difference in what arrives is a
    difference in the hash, and a re-publish of unchanged data produces the same
    number on any machine (Python's `hash()` is salted per process and cannot).

    31 bits, not 64, so `sum(row_hash)` stays exact in SQLite's int64 no matter
    how big a table gets: 41,294 scrobbles x 2^31 is 2^46, and ten million rows
    would still be 2^54.
    """
    return int(hashlib.sha1(rendered.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


def _emit_table(con, parquet, table, out_dir) -> tuple[str, dict]:
    cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()
    names = [c[0] for c in cols]
    if ROW_HASH in names:
        # Fail rather than emit two columns of one name. No loader writes this
        # today, and the envelope cannot (payload keys that collide with it are
        # renamed p_*), so reaching here means a zone invented the name and the
        # digest would silently stop being a digest of the row.
        raise SystemExit(
            f"publish-cf: {table} already has a `{ROW_HASH}` column — rename it "
            "or the load digest cannot be trusted")
    ddl_cols = ", ".join(
        f'"{n}" {_TYPE_MAP.get(t.upper(), "TEXT")}' for n, t, *_ in cols
    ) + f', "{ROW_HASH}" INTEGER'
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
    collist = ", ".join(f'"{n}"' for n in names) + f', "{ROW_HASH}"'
    header = f'INSERT INTO "{table}" ({collist}) VALUES\n'
    lines = [f'DELETE FROM "{table}";']  # re-import is idempotent

    # The key a diff would use, checked rather than assumed (ADR-0026 §1). It is
    # only RECORDED here — nothing keys off it yet — because the answer for the
    # served slice is not the answer publish.py reports for the whole record:
    # t1_notes is 3,444 rows and 1,984 distinct ids there, and the published
    # table is a different, smaller set of rows.
    key_at = [names.index(c) for c in ("id", "origin_ref") if c in names]
    seen: set[tuple] = set()
    duplicate_keys = 0

    digest = 0
    batch: list[str] = []
    size = len(header)
    for r in rows:
        # The hash covers the row's own values and not itself, so it is stable
        # under re-publish. The rendered text is reused for both, never rebuilt.
        body = ", ".join(_sql_literal(v) for v in r)
        h = _row_hash(body)
        digest += h
        tup = f"({body}, {h})"
        if key_at:
            k = tuple(r[i] for i in key_at)
            if k in seen:
                duplicate_keys += 1
            else:
                seen.add(k)
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
    return "\n".join(ddl), {
        "rows": len(rows),
        "digest": digest,
        "key": [names[i] for i in key_at],
        "duplicate_keys": duplicate_keys,
    }


# Every vector kind the blob is made of. `vectors.f32` is ONE artifact — the
# Worker loads it whole and indexes into it — so it cannot be built a piece at a
# time, and a run that holds only some of these cannot produce a valid one.
VEC_KINDS = ("t2_atom_vec", "t2_note_vec", "t2_post_vec")


def _emit_vectors(con, out, *, partial: bool) -> dict:
    """One flat float32 blob plus a sidecar whose row i is blob row i.

    `partial` changes what a MISSING kind means, and the difference matters
    enough to be the reason this argument exists.

    On a full run, missing means "not built yet" — a zone can be added to the
    manifest and reach D1 before its vectors exist — and skipping costs recall
    on one kind while a crash costs the whole nightly.

    On a scoped run it may instead mean "this lane did not rebuild it", and
    skipping then does something far worse than cost recall: it writes a blob
    with that kind absent, which the instance uploads over a complete one. A
    notes lane would delete post search from the surface and report success.
    A scoped run therefore emits vectors only when it holds every kind, and
    says so in the manifest either way so the upload can be conditional rather
    than hopeful.
    """
    blob = array("f")
    index: list[dict] = []
    dim = None
    missing: list[str] = []

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
            missing.append(f"t2_{kind}_vec")
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

    if partial and missing:
        # No file, deliberately. An incomplete blob on disk is something a
        # workflow uploads; an absent one is something it has to notice.
        print(f"  R2  vectors: NOT WRITTEN — this scoped run holds no "
              f"{', '.join(missing)}, and the blob is all-or-nothing. "
              "Uploading it would delete those vectors from the surface.")
        return {"complete": False, "missing": missing, "count": 0, "dim": dim, "bytes": 0}

    if sys.byteorder != "little":
        blob.byteswap()  # the Worker reads little-endian Float32Array
    (out / "vectors.f32").write_bytes(blob.tobytes())
    (out / "vectors.json").write_text(
        json.dumps({"dim": dim, "count": len(index), "normalized": True, "rows": index}),
        encoding="utf-8",
    )
    return {"complete": True, "count": len(index), "dim": dim, "bytes": len(blob) * 4}



IMPORT_SH = r"""#!/bin/sh
# Load this bundle into D1, making the database MATCH it exactly.
#
# Usage: [WRANGLER="npx wrangler"] ./import.sh <d1-database-name>
#
# A full bundle reconciles first: any table in D1 not in served-tables.txt is
# dropped. A partial one drops nothing — see bundle-scope.txt.
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

# Which kind of bundle this is. Refuse on anything unrecognised: the answer
# decides whether this run may DELETE tables, and a default either way is wrong.
# Defaulting to full would let a lane's bundle wipe every zone it did not carry;
# defaulting to partial would quietly stop the manifest from being able to
# revoke a zone, which is the failure the reconcile was built for.
SCOPE="$(cat "$HERE/bundle-scope.txt" 2>/dev/null || true)"
case "$SCOPE" in
  full|partial) ;;
  *) echo "import.sh: bundle-scope.txt says '${SCOPE:-<missing>}' — refusing." >&2
     echo "  A bundle must state whether it is 'full' or 'partial'; that is what" >&2
     echo "  decides whether tables absent from it get dropped." >&2
     exit 1 ;;
esac

LIVE="$(mktemp)"
ACTUAL=""; MISMATCH=""
trap 'rm -f "$LIVE" "$ACTUAL" "$MISMATCH"' EXIT

if [ "$SCOPE" = "partial" ]; then
  # A scoped run recomputed some zones and knows nothing about the rest. Its
  # table list is not a claim that the others should not exist, so there is
  # nothing here to reconcile against and nothing may be dropped. Row-level
  # tightening still applies within the tables it does carry: every data file
  # opens with DELETE FROM.
  echo "== partial bundle: loading $(grep -c . "$HERE/bundle-tables.txt") table(s), dropping nothing =="
else
echo "== reconciling $DB against served-tables.txt =="
# Materialise the table list FIRST. Piping the query straight into the loop hides
# its exit status behind the pipeline, so a failing reconcile would fall through
# to loading data into a database that was never reconciled.
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
fi

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
# Rows written by every call below, summed. Printed at the end; see apply_file.
WRITTEN=0
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

  # The meter. D1 bills rows WRITTEN, wrangler reports them per call, and this
  # script threw that number away on success — so the cost of a load was only
  # ever knowable by arithmetic. It is a running total and never a gate: a
  # format change here must leave the import alone, hence the `|| true` and the
  # `+ 0`. `grep -o`, not `sed`, because a one-line JSON body would hide every
  # match but the last.
  n=$(printf '%s' "$out" | grep -o '"rows_written" *: *[0-9]*' 2>/dev/null \
      | grep -o '[0-9]*$' | awk '{s += $1} END {print s + 0}' || true)
  WRITTEN=$(( WRITTEN + ${n:-0} ))
}

echo "== schema =="
# Through apply_file like every other write. It used to be a bare wrangler call,
# which meant the one statement that must land before any data had no busy-queue
# handling and no place in the meter — a D1 still draining the previous import
# failed the whole run here rather than waiting the five seconds it needed.
apply_file "$HERE/schema.sql"

echo "== data =="
# Batched, not one call per table. Each `wrangler d1 execute` spawns node and
# pays a full round trip, so 18 tables meant 18 startups against remote D1 —
# minutes of the deploy were process launches. Files are concatenated up to a
# size budget, keeping whole tables together so a failure is still attributable.
BATCH="$(mktemp)"
: > "$BATCH"
BUDGET=4000000
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
echo "== verifying rows and digest =="
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
  # expected-counts.txt is `table|rows|digest`, and verify.sql returns
  # "<table>": "<rows>|<digest>" — one subquery per table carrying both facts.
  #
  # The digest is the half that is new and the half that matters. A count proves
  # how MANY rows arrived; the sum of the per-row hashes proves WHICH. A batch
  # that loaded yesterday's rows, or half a table twice, satisfies the count
  # exactly and is the failure the count cannot see.
  while IFS="|" read -r t want_n want_h; do
    [ -n "$t" ] || continue
    got=$(sed -n "s/.*\"$t\" *: *\"\([0-9|]*\)\".*/\1/p" "$ACTUAL" | head -1)
    if [ -z "$got" ]; then
      echo "$t|$want_n rows|absent" >> "$MISMATCH"
      continue
    fi
    got_n=${got%%|*}
    got_h=${got##*|}
    if [ "$got_n" != "$want_n" ]; then
      echo "$t|$want_n rows|$got_n rows" >> "$MISMATCH"
    elif [ "$got_h" != "$want_h" ]; then
      echo "$t|digest $want_h|digest $got_h" >> "$MISMATCH"
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
    # still fit inside the job's timeout. Overridable ONLY so this path can be
    # tested at all: three rounds at the real pause is a minute, which is why it
    # went untested until the digest gave it something to catch.
    sleep $(( attempt * ${D1_REPAIR_PAUSE:-10} ))
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

# What the load cost at the meter D1 actually bills. Printed always, because a
# number nobody sees is a number nobody acts on: this load is a full rewrite, so
# it spends the corpus every night whatever the delta was (ADR-0026).
echo "== done: $DB now matches this bundle — $WRITTEN rows written =="
"""


# D1 keeps its own bookkeeping in the same database. Reconciliation drops
# anything not served, so these prefixes must be protected or the first import
# destroys the database it is loading into.
# `wh_` is worker-owned state (the wh_audit call log). It is not in the
# published set, so without this the first reconcile would delete the only
# record of who called what.
_PROTECTED_PREFIXES = ("sqlite_", "_cf_", "d1_", "wh_")


def _emit_reconcile(out, tables: dict[str, dict], scope: str) -> None:
    """Make the bundle authoritative over D1, not merely additive.

    Row-level tightening already revokes: every data file opens with DELETE FROM,
    so holding a folder or a path zone removes those rows on the next import.
    Table-level tightening did not — a zone flipped to `hold` simply stopped being
    mentioned, and D1 kept the table and every row in it, quietly, while the
    manifest said held.

    So a FULL bundle ships the served list and an importer that reconciles
    against it: anything in D1 that is not on the list is dropped, including
    tables left by an older manifest or created by hand. Fail-closed applied to
    deletion.

    **Only a full bundle may drop a table.** A scoped run knows what it
    recomputed and nothing about the rest, so its table list is not a statement
    that the others should not exist — and reconciling against it would delete
    the entire record on every lane run. `served-tables.txt` is therefore the
    full bundle's artifact alone, and its absence is what stops the drop.

    Absence is not left to carry that meaning by itself, though. Three files
    ship, and the importer refuses when the first one is missing or unrecognised:

      bundle-scope.txt    `full` or `partial`. Neither mode is the absent case.
      bundle-tables.txt   what THIS bundle carries. Always written; what the
                          shrink guard reads, so a scoped run guards its own
                          tables and nothing it knows nothing about.
      served-tables.txt   the reconcile authority. Full bundles only.
    """
    served_tables = sorted(tables)
    (out / "bundle-scope.txt").write_text(scope + "\n", encoding="utf-8")
    (out / "bundle-tables.txt").write_text("\n".join(served_tables) + "\n",
                                           encoding="utf-8")
    if scope == "full":
        (out / "served-tables.txt").write_text("\n".join(served_tables) + "\n",
                                               encoding="utf-8")

    # What the import reads back after loading. Shipped as data rather than
    # derived in the shell, because the shell cannot know what the bundle meant
    # to contain — only what D1 happens to hold, which is the thing in question.
    # `table|rows|digest`. The digest is the sum of the per-row hashes this
    # bundle wrote, so the read-back checks WHICH rows arrived and not merely how
    # many. On 2026-08-19 a lost batch was caught by the count; a batch that
    # loaded the right number of wrong rows would not have been.
    (out / "expected-counts.txt").write_text(
        "".join(f"{t}|{tables[t]['rows']}|{tables[t]['digest']}\n"
                for t in served_tables), encoding="utf-8")

    # One query, one round trip: a single row whose COLUMN NAMES are the table
    # names, so the shell matches a count to its table without depending on
    # column order or on how wrangler formats its JSON.
    #
    # Scalar subqueries, not `UNION ALL`. The obvious compound SELECT is what
    # this was written as first, and D1 rejects it outright — "too many terms in
    # compound SELECT: SQLITE_ERROR" — at a limit far below SQLite's documented
    # 500. It passed against a stubbed wrangler and failed against the real one.
    #
    # Both facts come back in ONE subquery per table, joined by `|`, rather than
    # two columns per table. That keeps this query the exact shape production has
    # already proved — 34 scalar subqueries, not 68 — because the limit that bit
    # here once was a limit on how many terms D1 would take, and there is no
    # reason to go back and find the next one.
    (out / "verify.sql").write_text(
        "SELECT\n" + ",\n".join(
            f'  (SELECT count(*) || \'|\' || COALESCE(sum("{ROW_HASH}"), 0) '
            f'FROM "{t}") AS "{t}"' for t in served_tables
        ) + ";\n", encoding="utf-8")

    script = IMPORT_SH
    sh = out / "import.sh"
    sh.write_text(script, encoding="utf-8")
    sh.chmod(0o755)


def run() -> int:
    src = config.SERVE
    # Any parquet, not t1_notes specifically. A scoped run publishes whatever
    # zones it rebuilt, and a lane that only touches scrobbles has no notes to
    # show for it — that is the lane working, not a missing projection.
    if not any(src.glob("*.parquet")):
        print("publish-cf: no serve projection — run `exo publish` first")
        return 1

    # What `exo publish` decided this run was allowed to emit. A projection with
    # no marker predates `--only` and is a full one; anything else is a refusal,
    # because guessing the scope is guessing whether it is safe to drop tables.
    scope_file = src / "_scope.json"
    if scope_file.exists():
        meta = json.loads(scope_file.read_text(encoding="utf-8"))
        scope = meta.get("scope", "")
        rebuilt = set(meta.get("rebuilt") or [])
        if scope not in ("full", "partial"):
            print(f"publish-cf: REFUSING — _scope.json says scope={scope!r}, "
                  "which is neither 'full' nor 'partial'")
            return 1
    else:
        scope, rebuilt = "full", set()

    out = src / "cf"
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    con = duckdb.connect(":memory:")
    try:
        schema: list[str] = []
        tables: dict[str, dict] = {}
        for p in sorted(src.glob("*.parquet")):
            if p.stem.endswith("_vec"):
                continue  # vectors do not go to D1
            # A scoped run emits ONLY what it recomputed. The projection also
            # holds zones carried across from the last full run so the brief
            # stays whole (see publish.run), and importing those would push a
            # copy from last night over whatever a lane loaded an hour ago.
            if scope == "partial" and p.stem not in rebuilt:
                continue
            ddl, info = _emit_table(con, p, p.stem, out / "data")
            schema.append(ddl)
            tables[p.stem] = info
            # The duplicate count is reported, not enforced. ADR-0026 §1 keys a
            # diff on (id, origin_ref) and CHECKS rather than assumes it — this
            # is that check, landed while the load is still a full rewrite, so
            # the answer arrives from production before anything depends on it.
            dup = info["duplicate_keys"]
            print(f"  D1  {p.stem:<18}{info['rows']:>8,} rows"
                  + (f"   — {dup:,} rows share a key with another"
                     if dup else ""))

        if not tables:
            print("publish-cf: REFUSING — the scope names no table this "
                  "projection holds; the bundle would be empty")
            return 1

        (out / "schema.sql").write_text("\n".join(schema) + "\n", encoding="utf-8")

        # The brief rides along as an MCP resource — the one artifact a client
        # loads without being asked.
        # Full bundles only. The brief describes the WHOLE record, and a scoped
        # run builds it partly from carried counts — shipping that would let a
        # lane republish a brief whose numbers are last night's for every zone
        # it did not touch.
        src_brief = src / "brief.md"
        if scope == "full" and src_brief.exists():
            (out / "brief.md").write_text(src_brief.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  R2  brief.md        {len(src_brief.read_bytes()):>8,} bytes")
        # The publicity axis, already resolved by `publish` (ADR-0019). Copied
        # rather than recomputed: this module changes shape and re-decides no
        # policy, and a grade worked out twice is a grade that will differ once.
        src_exposure = src / "exposure.json"
        if src_exposure.exists():
            (out / "exposure.json").write_text(
                src_exposure.read_text(encoding="utf-8"), encoding="utf-8")
            n = len(json.loads(src_exposure.read_text(encoding="utf-8"))["zones"])
            print(f"  R2  exposure.json   {n:>8,} zones graded")
        else:
            # Not fatal: the worker fails closed on a missing file (every zone
            # private), so an old projection serves tight rather than open.
            print("  R2  exposure.json: absent — the surface will treat every zone as private")
        # The tool list, likewise already resolved (ADR-0020). Note the opposite
        # failure direction to exposure.json above, and it is not a slip: this
        # file cannot widen what leaves — the projection decides that by
        # omission — so an absent one means "offer everything the engine has"
        # rather than "offer nothing".
        src_surface = src / "surface.json"
        if src_surface.exists():
            (out / "surface.json").write_text(
                src_surface.read_text(encoding="utf-8"), encoding="utf-8")
            doc = json.loads(src_surface.read_text(encoding="utf-8"))
            print(f"  R2  surface.json    {len(doc['tools']):>8,} tools offered"
                  + (f", {len(doc['withheld'])} withheld" if doc.get("withheld") else ""))
        else:
            print("  R2  surface.json: absent — the surface will offer every tool it defines")
        _emit_reconcile(out, tables, scope)
        vinfo = _emit_vectors(con, out, partial=(scope == "partial"))
        if vinfo.get("complete"):
            print(f"  R2  vectors.f32     {vinfo['count']:>8,} x {vinfo['dim']}d "
                  f"= {vinfo['bytes'] / 1e6:.2f} MB")

        (out / "MANIFEST.json").write_text(json.dumps({
            "d1_tables": {t: i["rows"] for t, i in tables.items()},
            "d1_rows_total": sum(i["rows"] for i in tables.values()),
            # What a diff would key on, and whether it could (ADR-0026 §1).
            "d1_keys": {t: {"key": i["key"], "duplicate_keys": i["duplicate_keys"]}
                        for t, i in tables.items()},
            "vectors": vinfo,
            "cosine": "vectors are unit-norm; similarity = dot product",
            "scope": scope,
            "reconciles": ("import.sh drops any D1 table not in served-tables.txt"
                           if scope == "full" else
                           "partial bundle: import.sh loads only bundle-tables.txt and drops nothing"),
        }, indent=2), encoding="utf-8")
        print(f"  bundle -> {out}")
        return 0
    finally:
        con.close()
