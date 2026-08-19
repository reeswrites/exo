"""Capture write sink — Life Terminal's reversible "save to brain" lands here.

second-brain wrote captures into `data/chat-logs/` (a non-grounding zone). As second-brain
is retired Exo owns them instead: one markdown file per capture under `captures/`,
`grounds: false`, `source: life-terminal` — the same reversible, one-file, provenance-stamped
contract, in the store that outlives second-brain. Nothing here grounds; a capture graduates
into the record only by an explicit promote, never on its own.

`cache_captures` (see loaders/garden.py) projects these for display (recentCaptures).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "capture").lower()).strip("-")[:50]
    return s or "capture"


def write_capture(title: str, body: str, iso_now: str | None = None) -> dict:
    """Write one reversible capture markdown file. Returns {ok, path} (path is repo-relative)."""
    config.CAPTURES.mkdir(parents=True, exist_ok=True)
    now = datetime.fromisoformat(iso_now) if iso_now else datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M:%S +0000")
    t = (title or (body or "").split("\n")[0] or "capture").strip()[:80]
    rand = hashlib.sha256(f"{now.isoformat()}|{t}|{body[:80]}".encode()).hexdigest()[:8]
    out = config.CAPTURES / f"{date}-{_slug(t)}-{rand}.md"
    front = (
        "---\n"
        "type: capture\n"
        f"title: {t!r}\n"
        f"date: {stamp}\n"
        "source: life-terminal\n"
        "grounds: false\n"
        "---\n\n"
        f"{body or ''}\n"
    )
    out.write_text(front, encoding="utf-8")
    return {"ok": True, "path": str(out.relative_to(config.ROOT)), "title": t}
