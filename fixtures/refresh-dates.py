#!/usr/bin/env python3
"""Slide the fixture's dates forward so its windowed sections stay non-empty.

    uv run python fixtures/refresh-dates.py [--check]

Two published sections window on the last 90 days — conversation topics, and
the workshop's recent activity. The fixture's material is dated, so those
sections drain as the fixture ages and eventually publish empty. Nothing errors
when that happens, which is the problem: a fixture that quietly stops
exercising a code path still passes.

So the dates move rather than the code. This shifts every date in the fixture by
a whole number of days, keeping every interval between them exactly as it was —
the relationships are what the fixture encodes, not the absolute days.

`--check` reports without writing, and exits 1 when a refresh is due. That is
what the test calls.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "instance"
WINDOW_DAYS = 90
# Refresh when the newest thing is older than this. Well inside the window, so
# the fixture is never one slow week away from testing nothing.
STALE_AFTER_DAYS = 45

# No trailing \b: an ISO timestamp is "2026-06-19T02:00:00", and T is a word
# character, so a word boundary there never matches and every commit date in
# the fixture went unseen. Guard against a longer number instead.
DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".csv", ".toml", ".xml"}
# .xml is the MyAnimeList export. Left out, its dates would sit still while
# everything around them slid forward, and the anime shelf would drift into
# looking abandoned relative to the Trakt history it is joined against.


def _files() -> list[Path]:
    skip = {"zones", "catalog"}
    return [p for p in sorted(ROOT.rglob("*"))
            if p.is_file() and p.suffix in TEXT_SUFFIXES
            and not any(part in skip for part in p.relative_to(ROOT).parts)]


def _max_date(text: str) -> dt.date | None:
    best = None
    for m in DATE.finditer(text):
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if best is None or d > best:
            best = d
    return best


def windowed() -> dict[str, dt.date]:
    """The newest date in each source that a 90-day window actually reads.

    Deliberately not the newest date anywhere in the fixture: a single
    future-dated slot or event would report the whole fixture fresh while the
    conversation topics and the workshop activity quietly drained to nothing.
    Ask the two sources that are windowed, and ask them separately.
    """
    out: dict[str, dt.date] = {}
    chats = [d for p in (ROOT / "raw/vault/chat-logs").glob("*.md")
             if (d := _max_date(p.name))]
    if chats:
        out["chat logs"] = max(chats)
    projects = ROOT / "raw/projects.json"
    if projects.exists():
        commits = json.loads(projects.read_text()).get("commits", [])
        dates = []
        for c in commits:
            d = _max_date(str(c.get("date", "")))
            if d:
                dates.append(d)
        if dates:
            out["workshop commits"] = max(dates)
    return out


def newest() -> dt.date | None:
    w = windowed()
    return min(w.values()) if w else None


def shift(days: int) -> int:
    """Move every date forward by `days`, filenames included."""
    def bump(m: re.Match) -> str:
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return m.group(0)
        return (d + dt.timedelta(days=days)).isoformat()

    touched = 0
    for p in _files():
        text = p.read_text(encoding="utf-8")
        new = DATE.sub(bump, text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            touched += 1
        if DATE.search(p.name):
            renamed = p.with_name(DATE.sub(bump, p.name))
            if renamed != p:
                p.rename(renamed)
    return touched


def main(argv: list[str]) -> int:
    check = "--check" in argv
    today = dt.date.today()
    for label, d in sorted(windowed().items()):
        print(f"  {label:18s} newest {d}  ({(today - d).days} days old)")
    top = newest()
    if top is None:
        print("no windowed dates found in the fixture — nothing to do")
        return 0
    age = (today - top).days
    if age <= STALE_AFTER_DAYS:
        print(f"fresh — refresh when it passes {STALE_AFTER_DAYS} days")
        return 0
    if check:
        print(f"STALE: the fixture's newest material is {age} days old, and two "
              f"published sections window on {WINDOW_DAYS} days.\n"
              f"Run: uv run python fixtures/refresh-dates.py")
        return 1
    # Land the newest item a few days back rather than exactly today: "today"
    # in a fixture reads as a coincidence, and a few days of slack keeps the
    # relative shapes (this week vs last week) honest.
    days = age - 3
    touched = shift(days)
    print(f"shifted every date forward {days} days across {touched} files; "
          f"newest is now {top + dt.timedelta(days=days)}")
    print("rebuild the fixture: EXO_HOME=fixtures/instance uv run exo rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
