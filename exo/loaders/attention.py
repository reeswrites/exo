"""T0 attention-thread loader — `t0_attention_thread` from the fetcher's cache.

What the owner has been turning over across chats and days (see
`scripts_impl.fetch_attention_threads`). Keyed on the Notion page id, not the
payload: a thread's summary and touch count change every time it moves, and
re-minting its id on each move would make one thread look like many.

`author="machine"`: the title, summary and moves are written by a skill reading
the chats, not typed by the owner. The surface says so on every row.
"""
from __future__ import annotations

import json

from .. import config
from ..provenance import Row, stable_id

CACHE = "attention-threads-cache.json"


def load() -> list[Row]:
    path = config.EXPORTS / CACHE
    if not path.exists():
        return []
    try:
        threads = json.loads(path.read_text(encoding="utf-8")).get("threads", [])
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        print(f"  attention_thread: {CACHE} is unreadable — skipping")
        return []
    rows = [
        Row(
            tier="t0", zone="attention_thread", source="notion/attention-threads",
            author="machine", grounds=False,
            created=t.get("last_touched") or t.get("first_seen"),
            origin_ref=t.get("page_id", ""),
            id=stable_id("attention_thread", t.get("page_id", "")),
            payload={
                "title": t.get("title", ""),
                "summary": t.get("summary", ""),
                "moves": t.get("moves", ""),
                # `<chat uuid> <title>` per line; the surface turns each into a
                # claude.ai link, so a thread leads back to its chats verbatim.
                "chats": t.get("chats", ""),
                "touches": int(t.get("touches") or 0),
                "load": t.get("load", ""),
                "first_seen": t.get("first_seen"),
                "last_touched": t.get("last_touched"),
                "authorship": t.get("authorship", ""),
            },
        )
        for t in threads if t.get("page_id")
    ]
    print(f"  attention_thread: {len(rows):,} threads from {CACHE}")
    return rows
