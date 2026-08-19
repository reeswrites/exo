"""Rebuild the store, and prove T2 is deterministically regenerable.

`run()`    full rebuild: ingest T0 -> index T1 -> derive T2 -> build catalog.
`verify()` the anti-collapse proof: derive T2 twice and assert the outputs are
           identical. If derivation were secretly reading its own prior output,
           or were nondeterministic, the digests would drift. They don't.
"""
from __future__ import annotations

import glob
import hashlib
import os

import pyarrow.parquet as pq

from .. import catalog, config, io, t2
from ..loaders import t0_loaders, t1_index


def _ingest_t0() -> None:
    for name, contributors in t0_loaders().items():
        rows = []
        for who, loader in contributors:
            add = loader()
            rows += add
            if len(contributors) > 1:
                print(f"  t0/{name:10s} + {len(add):>5,} from {who}")
        io.write_zone("t0", name, rows)
        print(f"  t0/{name:10s} {len(rows):>7,}")


def _digest_t2() -> dict[str, str]:
    """Content digest of each t2 parquet — semantic, not byte (ignores file meta)."""
    out: dict[str, str] = {}
    for p in sorted(glob.glob(str(config.T2 / "*.parquet"))):
        table = pq.read_table(p)
        # stable digest over sorted (column, values) — order-independent of metadata
        h = hashlib.sha256()
        for col in sorted(table.column_names):
            h.update(col.encode())
            for v in table.column(col).to_pylist():
                h.update(b"\x1f")
                h.update(str(v).encode())
        out[os.path.basename(p)] = h.hexdigest()[:16]
    return out


def run() -> None:
    from . import sync_raw
    print("rebuild: sync-raw (refresh MUST-STAY upstream mirrors)")
    sync_raw.run()
    print("rebuild: T0 ingest")
    _ingest_t0()
    print("rebuild: T1 index")
    t1_index.run()
    print("rebuild: T2 derive (nuke first)")
    for p in glob.glob(str(config.T2 / "*.parquet")):
        os.remove(p)
    t2.run_all()
    names = catalog.build()
    print(f"rebuild: catalog {len(names)} views")


def verify() -> None:
    """Prove T2 regenerates identically. Derives ONCE against what is on disk.

    This used to derive twice from scratch, ignoring the derivation `rebuild` had
    just done — three full passes a night to answer a question two can answer.
    If T2 is absent (verify run standalone) it derives once to establish the
    baseline, so the standalone contract is unchanged.
    """
    first = _digest_t2()
    if not first:
        print("verify: no T2 on disk — deriving a baseline first...")
        t2.run_all()
        first = _digest_t2()
    print("verify: re-deriving T2 once to prove determinism...")
    t2.run_all()
    second = _digest_t2()
    ok = first == second and bool(first)
    for name in sorted(first):
        mark = "==" if first[name] == second.get(name) else "!!"
        print(f"  {mark} {name}: {first[name]} / {second.get(name)}")
    if ok:
        print("verify: PASS — T2 regenerates identically from T0+T1 (anti-collapse holds)")
    else:
        raise SystemExit("verify: FAIL — T2 is not deterministically regenerable")
