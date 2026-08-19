"""The ONE provenance schema every row in every zone carries.

This is the thing the three repos never agreed on (second-brain `author:`,
alchemy `grounds:false`, taste-engine `Provenance`). One envelope they all map
onto. Payload columns are zone-specific; the envelope is universal.

The load-bearing fields:
  tier        t0 | t1 | t2         which stratum
  author      external|human|machine  who authored the bytes
  grounds     bool                 may this be cited/read AS SOURCE by derivation?
  regenerable bool                 can it be rebuilt from lower tiers? (t2 => True)

The wall (ADR-0001, Fig 4): derivation may only read rows where grounds=True
(t0 + t1). Nothing reads t2 as source. Enforced structurally in catalog.py.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any

# envelope column order — every zone table starts with these
ENVELOPE = ["id", "tier", "zone", "source", "author", "created", "origin_ref", "grounds", "regenerable"]

TIERS = {"t0", "t1", "t2"}
AUTHORS = {"external", "human", "machine"}


def stable_id(source: str, *natural_key: Any) -> str:
    """Content-derived id: same bytes -> same id -> regeneration is idempotent.

    Mirrors second-brain's ADR-0009 (id = hash(span + source)).
    """
    h = hashlib.sha1()
    h.update(source.encode())
    for k in natural_key:
        h.update(b"\x1f")  # unit separator, avoids "ab"+"c" == "a"+"bc"
        h.update(str(k).encode())
    return h.hexdigest()[:16]


@dataclass
class Row:
    """One record = envelope + payload. `.flat()` merges them for parquet."""

    tier: str
    zone: str
    source: str
    author: str
    payload: dict[str, Any]
    created: str | None = None       # ISO8601 string, nullable
    origin_ref: str = ""             # path/URL back to the source of record
    grounds: bool = True             # t2 producers override to False
    regenerable: bool = False        # t2 producers override to True
    id: str = field(default="")

    def __post_init__(self) -> None:
        assert self.tier in TIERS, f"bad tier {self.tier}"
        assert self.author in AUTHORS, f"bad author {self.author}"
        if not self.id:
            # natural key = sorted payload values, so id is deterministic
            self.id = stable_id(self.source, self.zone, *[self.payload[k] for k in sorted(self.payload)])

    def flat(self) -> dict[str, Any]:
        env = {
            "id": self.id, "tier": self.tier, "zone": self.zone, "source": self.source,
            "author": self.author, "created": self.created, "origin_ref": self.origin_ref,
            "grounds": self.grounds, "regenerable": self.regenerable,
        }
        # Envelope ALWAYS wins. A payload key that collides with a provenance
        # column (e.g. goodreads' book `author`) would otherwise clobber
        # provenance — corrupting the `rows` view. Rename collisions to p_<key>.
        for k, v in self.payload.items():
            env[f"p_{k}" if k in env else k] = v
        return env
