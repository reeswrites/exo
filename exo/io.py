"""Write a zone table to parquet. Tier-native storage; DuckDB reads it later.

One function: `write_zone(tier, name, rows)`. Everything a loader produces goes
through here so the envelope columns always lead and provenance is uniform.

It also stamps **first_seen** on T0: the moment this store first observed a row,
which is not the same fact as when the thing happened. Many sources carry no
date at all — a bookmark, an untimed checkin — and for those "we first saw it on
the 18th" is the only temporal handle there is. Running nightly makes it a good
one: the answer is right to within a day.

The stamp is set ONCE and never rewritten. That is the whole value: T0 parquet is
regenerated from scratch on every rebuild, so a timestamp computed at write time
would reset to today on every run and mean nothing. The ledger lives outside
every zone that gets rebuilt, and write_zone only ever adds ids to it.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import config
from .provenance import ENVELOPE, Row

_TIER_DIR = {"t0": config.T0, "t1": config.T1, "t2": config.T2}


def _load_ledger() -> dict[str, str]:
    if not config.FIRST_SEEN.exists():
        return {}
    t = pq.read_table(config.FIRST_SEEN)
    return dict(zip(t.column("id").to_pylist(), t.column("first_seen").to_pylist()))


def _stamp_first_seen(flats: list[dict]) -> None:
    """Add `first_seen` to each row, minting it only for ids never seen before.

    T2 is excluded by the caller: its ids are content-derived and its whole point
    is to regenerate identically, so an ingestion timestamp there would be noise
    that `wh verify` would have to learn to ignore.
    """
    ledger = _load_ledger()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = 0
    for f in flats:
        rid = f.get("id")
        if rid is None:
            continue
        if rid not in ledger:
            ledger[rid] = now
            new += 1
        f["first_seen"] = ledger[rid]

    if new:
        config.ensure_dirs()
        pq.write_table(
            pa.table({"id": list(ledger.keys()), "first_seen": list(ledger.values())}),
            config.FIRST_SEEN,
        )


def write_zone(tier: str, name: str, rows: list[Row]) -> Path:
    """Serialize rows -> zones/<tier>/<name>.parquet, envelope columns first.

    Empty row sets still write an empty file so the catalog view resolves.
    """
    config.ensure_dirs()
    out = _TIER_DIR[tier] / f"{name}.parquet"
    flats = [r.flat() for r in rows]
    if not flats:
        # empty table with just the envelope so downstream views don't break
        table = pa.table({c: pa.array([], type=pa.string()) for c in ENVELOPE})
        pq.write_table(table, out)
        return out
    # union of all keys, envelope first then payload keys in first-seen order
    payload_keys: list[str] = []
    for f in flats:
        for k in f:
            if k not in ENVELOPE and k not in payload_keys:
                payload_keys.append(k)
    if tier == "t0":
        _stamp_first_seen(flats)
        if "first_seen" not in payload_keys:
            payload_keys.append("first_seen")
    cols = ENVELOPE + payload_keys
    data = {c: [f.get(c) for f in flats] for c in cols}
    table = pa.table(data)
    pq.write_table(table, out)
    return out


def row_count(path: Path) -> int:
    return pq.read_metadata(path).num_rows if path.exists() else 0


def markdown(base, *, recursive: bool = False, exclude: set[str] | None = None):
    """Every .md file under `base`, sorted, minus the ones that are not writing.

    Dotfiles are always excluded, and that is not tidiness. macOS `tar` writes
    AppleDouble sidecars — `._name.md` — which a listing on macOS hides and
    which become real 163-byte binary files the moment the archive is opened on
    Linux. They match *.md. In CI they turned one draft into three, and the
    binary rows they produced were rejected by D1 without an error, so the
    publish reported success and the table stayed empty.

    Anything that globs a mirrored directory has this hazard; the fix belongs in
    one place rather than in ten.
    """
    it = base.rglob("*.md") if recursive else base.glob("*.md")
    skip = exclude or set()
    return [p for p in sorted(it) if not p.name.startswith(".") and p.name not in skip]
