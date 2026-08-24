"""`exo ledger` — move the one thing a rebuild cannot regenerate.

Everything else in the record is a function of its inputs: lose a zone and a
rebuild restores it. The ledger is not. It records *when this store first saw a
row*, which is a fact about an observation rather than about the data, and no
amount of re-reading the inputs recovers it.

That makes it the only state a cloud leg has to carry deliberately, and carrying
it deliberately is exactly what nobody did:

  surface-log.json     was gitignored and fetched by nothing, so every runner
                       treated its run as the first run ever, established a
                       fresh baseline and announced nothing as new. The
                       published brief lost "Recently added" entirely — the
                       laptop wrote a correct one and CI overwrote it nightly
                       with an amnesiac copy. Fixed by hand in the workflow.
  first_seen.parquet   the same file one directory over, and still broken. It
                       is gitignored, absent from the raw tarball (which packs
                       raw/ and items/), and fetched by no workflow step, while
                       `first_seen` is a published column on every T0 table. So
                       the cloud leg stamps a life of consumption with the date
                       of whichever run happened to build it.

Fixing that one file by name would have been the third time someone fixed this
one file by name. So the unit is the DIRECTORY: whatever `zones/_ledger/` holds
travels, and a ledger file added later rides along without anyone remembering.

## Merging, not replacing

`import` merges and **the earliest date wins**, because both ledgers answer
"when was this first seen" and the earliest answer is the true one. That makes
the operation commutative and idempotent: two legs that both ran, in either
order, converge on the same ledger, and re-importing changes nothing.

Replacing would be wrong in the direction that costs data. A lane that pulled
the ledger, minted ids and failed to push has *lost* those mints; the next run
re-mints them at a later date and the row's first_seen quietly moves forward. A
minimum cannot move forward.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .. import config

# Files whose merge rule is known. Both are `key -> ISO date`, earliest wins.
FIRST_SEEN = "first_seen.parquet"
SURFACE_LOG = "surface-log.json"


def _read_first_seen(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    t = pq.read_table(path)
    return dict(zip(t.column("id").to_pylist(), t.column("first_seen").to_pylist()))


def _write_first_seen(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"id": list(data.keys()), "first_seen": list(data.values())}), path)


def _read_json(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return blob if isinstance(blob, dict) else {}


def earliest(mine: dict[str, str], theirs: dict[str, str]) -> tuple[dict[str, str], int, int]:
    """Merge two `key -> date` maps keeping the earlier date. Returns (merged, added, pulled_back).

    ISO-8601 sorts lexicographically, which is the whole reason these stamps are
    written as strings — comparing them needs no parsing and therefore cannot
    fail on a format this function has not met.
    """
    merged = dict(mine)
    added = pulled_back = 0
    for k, v in theirs.items():
        if k not in merged:
            merged[k] = v
            added += 1
        elif v < merged[k]:
            merged[k] = v
            pulled_back += 1
    return merged, added, pulled_back


def export(dest: str) -> int:
    """Copy the whole ledger to `dest`, for an instance to ship wherever it keeps
    durable state. The engine does not know what that place is (ADR-0014)."""
    out = Path(dest).expanduser()
    if not config.LEDGER.exists():
        print(f"ledger export: nothing at {config.LEDGER} yet")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(config.LEDGER.iterdir()):
        if f.is_file():
            shutil.copy2(f, out / f.name)
            print(f"  {f.name:<22}{f.stat().st_size:>10,} bytes")
            n += 1
    print(f"ledger export: {n} file(s) -> {out}")
    return 0


def merge(src: str) -> int:
    """Merge another copy of the ledger into this one. Earliest date wins."""
    other = Path(src).expanduser()
    if not other.is_dir():
        print(f"ledger merge: {other} is not a directory")
        return 1
    config.LEDGER.mkdir(parents=True, exist_ok=True)

    theirs_fs = _read_first_seen(other / FIRST_SEEN)
    if theirs_fs:
        mine = _read_first_seen(config.LEDGER / FIRST_SEEN)
        merged, added, back = earliest(mine, theirs_fs)
        _write_first_seen(config.LEDGER / FIRST_SEEN, merged)
        print(f"  {FIRST_SEEN:<22}{len(merged):>8,} ids  (+{added:,} new, "
              f"{back:,} corrected to an earlier sighting)")

    theirs_log = _read_json(other / SURFACE_LOG)
    if theirs_log:
        mine_log = _read_json(config.LEDGER / SURFACE_LOG)
        merged_log, added, back = earliest(mine_log, theirs_log)
        (config.LEDGER / SURFACE_LOG).write_text(
            json.dumps(merged_log, indent=1, sort_keys=True), encoding="utf-8")
        print(f"  {SURFACE_LOG:<22}{len(merged_log):>8,} zones  (+{added:,} new, "
              f"{back:,} corrected)")

    # A ledger file this does not know how to merge must not be merged by
    # guessing. Copying it when absent is safe — there is nothing to lose a
    # conflict against — but silently picking a side when both exist is how a
    # ledger stops being the earliest answer. Say so, so the NEXT file added
    # here arrives with a rule instead of a coin flip.
    known = {FIRST_SEEN, SURFACE_LOG}
    for f in sorted(other.iterdir()):
        if not f.is_file() or f.name in known:
            continue
        local = config.LEDGER / f.name
        if local.exists():
            print(f"  {f.name:<22}  SKIPPED — no merge rule, and both copies exist. "
                  f"Add one to {__name__} rather than letting a run pick a side.")
        else:
            shutil.copy2(f, local)
            print(f"  {f.name:<22}  copied (absent locally; no merge needed)")
    return 0


def status() -> int:
    if not config.LEDGER.exists():
        print(f"ledger: nothing at {config.LEDGER}")
        return 0
    fs = _read_first_seen(config.LEDGER / FIRST_SEEN)
    log = _read_json(config.LEDGER / SURFACE_LOG)
    print(f"ledger: {config.LEDGER}")
    if fs:
        stamps = sorted(fs.values())
        print(f"  {FIRST_SEEN:<22}{len(fs):>8,} ids   ({stamps[0][:10]} .. {stamps[-1][:10]})")
    else:
        print(f"  {FIRST_SEEN:<22}       — absent. Every T0 row rebuilt from here "
              "will be stamped with today, which is the bug this exists to stop.")
    if log:
        print(f"  {SURFACE_LOG:<22}{len(log):>8,} zones (baseline {log.get('_baseline', '?')})")
    else:
        print(f"  {SURFACE_LOG:<22}       — absent; this run would read as the first ever")
    return 0
