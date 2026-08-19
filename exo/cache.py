"""The cache namespace (ADR-0002) — joinable, disposable derived views.

Unlike a tier, a cache view carries NO rebuild contract: it is whatever its
engine last wrote, refreshed by rerunning that engine. `wh verify` never touches
it (it walks `zones/t2/` only), and the catalog registers `cache_<name>` in the
`full` profile ONLY — so the `source` wall can never see it and machine cache
output can never re-enter derivation.

Cache rows carry a LIGHT envelope, not the tier envelope: `id, zone, source,
author, created, grounds`. They are deliberately NOT tiered — a cache is not a
stratum, so it claims no `tier` and no `regenerable` (there is no regeneration
contract for it to be regenerable under). `grounds` is always False: nothing may
cite a cache. `author` is always machine.

Engines in other repos (alchemy — ADR-0036) call `cache.row(...)` then
`cache.write(name, rows)`; the import stays one-way (engine -> Exo) because
nothing here ever regenerates the cache.

    rows = [cache.row("returns", trail, source="alchemy.returns") for trail in trails]
    cache.write("returns", rows)          # -> zones/_cache/returns.parquet -> cache_returns
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from . import config
from .provenance import stable_id

# light envelope — every cache row leads with these; NOT the tier envelope
CACHE_ENVELOPE = ["id", "zone", "source", "author", "created", "grounds"]


def row(
    zone: str,
    payload: dict[str, Any],
    *,
    source: str,
    created: str | None = None,
    id: str | None = None,
) -> dict[str, Any]:
    """A stamped cache record (a plain dict, not a tier `Row`).

    `grounds` is always False (nothing may cite a cache); `author` is always
    machine. `id` defaults to a content id over the payload, so a re-run that
    produces the same payload produces the same id. Payload keys that collide
    with an envelope column are renamed `p_<key>` (as the tier envelope does).
    """
    rid = id or stable_id(source, zone, *[payload[k] for k in sorted(payload)])
    rec: dict[str, Any] = {
        "id": rid, "zone": zone, "source": source,
        "author": "machine", "created": created, "grounds": False,
    }
    for k, v in payload.items():
        rec[f"p_{k}" if k in rec else k] = v
    return rec


def write(name: str, rows: list[dict[str, Any]]) -> Path:
    """Serialize cache rows -> zones/_cache/<name>.parquet, envelope columns first.

    Overwrites: a cache view is whatever the last run wrote. An empty set still
    writes a header row-less file so the `cache_<name>` view resolves.
    """
    config.ensure_dirs()
    out = config.CACHE / f"{name}.parquet"
    if not rows:
        table = pa.table({c: pa.array([], type=pa.string()) for c in CACHE_ENVELOPE})
        pq.write_table(table, out)
        return out
    payload_keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in CACHE_ENVELOPE and k not in payload_keys:
                payload_keys.append(k)
    cols = CACHE_ENVELOPE + payload_keys
    table = pa.table({c: [r.get(c) for r in rows] for c in cols})
    pq.write_table(table, out)
    return out
