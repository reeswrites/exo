"""One-shot: migrate the standalone `habits` repo onto the item spine (ADR-0002).

Type B was already the spine's design in miniature — a store of habit definitions
plus an append-only completion log. So the migration is a straight remap:

    habits.json[].{id,title,cadence,blockMinutes,dayParts,links,streak}
        -> items/habit/*.md  (id PRESERVED so identity + the log line up)
    log.json.entries[].{id,date}
        -> `done` events in items/_events/*.jsonl

The stored `streak` becomes `seed_streak` (the fallback compute_streak honours until
the first logged completion) so no streak is lost even with an empty completion log.

Idempotent: a habit whose id already has a file is skipped (no duplicate `created`
event); completions dedup per (id, date) in complete_habit. Safe to re-run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import config, item


def _src() -> Path:
    return Path(os.environ.get("HABITS_DATA_DIR", config.ROOT.parent / "habits" / "data"))


def run(src: Path | None = None) -> dict:
    base = src or _src()
    store = json.loads((base / "habits.json").read_text(encoding="utf-8"))
    habits = store.get("habits", [])
    try:
        log = json.loads((base / "log.json").read_text(encoding="utf-8")).get("entries", [])
    except FileNotFoundError:
        log = []

    existing = item._habits_by_id()
    created = 0
    for h in habits:
        if h["id"] in existing:
            continue  # already migrated
        item.create_habit(
            h["title"], h["cadence"],
            block_minutes=h.get("blockMinutes"), day_parts=h.get("dayParts"),
            links=h.get("links"), seed_streak=h.get("streak"), item_id=h["id"],
        )
        created += 1

    completions = 0
    for e in log:
        rec = item.complete_habit(e["id"], e["date"])
        # complete_habit returns the existing event on dedup; count only fresh writes
        if rec.get("event") == "done":
            completions += 1

    item.build_habit_glance()
    return {"src": str(base), "habits_created": created, "habits_total": len(habits),
            "completions_logged": completions}
