"""T0 meal log — what you ate, and how it turned out (ADR-0003 §2).

An append-only JSONL at second-brain/data/meals.jsonl, written by the
taste-engine `taste cooking --log` / `--rate` surface (the same cross-repo
authoring pattern dining uses for restaurant-visits.csv). Read here into two
T0 zones:

  meal_event   one row per eaten meal (mode, recipe|place, who, cost)
  meal_rating  one row per RATED meal (score, redo?, tweak) — references its
               event by event_id

Both grounds=True (a real observation the wall may read), author=human (you
logged it), tier=t0. Empty file is fine — write_zone still emits the envelope
so the catalog view resolves before the first meal is logged. Scores/penalties
are NOT computed here — the cooking vertical derives them on-ask (ADR-0002).
"""
from __future__ import annotations

import json

from .. import config
from ..provenance import Row, stable_id

_PATH = config.VAULT / "meals.jsonl"


def _records() -> list[dict]:
    if not _PATH.exists():
        return []
    out: list[dict] = []
    for line in _PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _event_id(rec: dict) -> str:
    """Stable per-event id. Prefer a client-supplied event_id (the logger writes
    one); fall back to hashing ts+ref so a hand-added line still gets a key."""
    if rec.get("event_id"):
        return str(rec["event_id"])
    return stable_id("meals", rec.get("ts", ""), rec.get("recipe_id", ""),
                     rec.get("place_id", ""))


def events() -> list[Row]:
    rows: list[Row] = []
    for rec in _records():
        eid = _event_id(rec)
        rows.append(Row(
            tier="t0", zone="meal_event", source="meals", author="human",
            created=(rec.get("ts") or None), origin_ref=eid, id=eid,
            payload={
                "event_id": eid,
                "mode": str(rec.get("mode", "")),          # cook | out
                "recipe_id": rec.get("recipe_id"),          # set when mode=cook
                "place_id": str(rec.get("place_id", "")),   # set when mode=out
                "who_with": str(rec.get("who_with", "")),
                "cost": rec.get("cost"),
                "notes": str(rec.get("notes", "")),
            },
        ))
    return rows


def ratings() -> list[Row]:
    rows: list[Row] = []
    for rec in _records():
        if rec.get("score") is None:
            continue  # only rated meals produce a rating row
        eid = _event_id(rec)
        rows.append(Row(
            tier="t0", zone="meal_rating", source="meals", author="human",
            created=(rec.get("ts") or None), origin_ref=eid,
            id=stable_id("meal_rating", eid),
            payload={
                "event_id": eid,
                "recipe_id": rec.get("recipe_id"),
                "score": rec.get("score"),                  # e.g. 1..5
                "redo": bool(rec.get("redo")),
                "tweak_notes": str(rec.get("tweak_notes", "")),
            },
        ))
    return rows
