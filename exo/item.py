"""The item spine — Type A tasks (ADR-0002).

Two write surfaces, split by grammar:
  - the NOUN (what a task IS) is a hand-editable markdown file under git:
    `items/task/<date>-<slug>-<id8>.md`, frontmatter = authored fields only.
  - the VERB (what happened) is one appended line to `items/_events/YYYY-MM.jsonl`.

Status is NEVER stored on the noun. It is the fold of the event log (last status
event wins, default "todo") — the same authority rule habits/glance.ts proves for
streaks. So a hand-edit to a task file can never desync its status; the log is the
sole authority, and `build_glance()` / the `t1_item` projection both read the fold.

`items/_glance/task.json` is the low-latency projection Kairos reads (no DuckDB).
`wh index` materializes `t1_item` + `t1_item_event` parquet for cross-zone SQL.
"""
from __future__ import annotations

import json
import re
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import config
from .provenance import stable_id

# Type A lifecycle. todo/doing are open; done/dropped are terminal (reopenable).
STATUSES = ("todo", "doing", "done", "dropped")
OPEN = ("todo", "doing")

# Authored frontmatter fields carried onto the task file + projected to t1_item.
# Status is deliberately absent — it lives in the event log only.
_AUTHORED = ("id", "family", "title", "created", "tags", "links",
             "done_when", "parent", "due", "est_minutes")


def _now_iso(iso_now: str | None = None) -> str:
    return iso_now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "task").lower()).strip("-")[:50]
    return s or "task"


def _rel(path: Path) -> str:
    """Store-relative path when under ROOT (the prod layout), else absolute."""
    try:
        return str(path.relative_to(config.ROOT))
    except ValueError:
        return str(path)


def _events_file(ts: str) -> Path:
    """Month-sharded event log: ts 2026-08-10T… -> _events/2026-08.jsonl."""
    return config.item_dirs()["events"] / f"{ts[:7]}.jsonl"


# ── write ──────────────────────────────────────────────────────────────────

def append_event(item_id: str, event: str, *, iso_now: str | None = None, **data) -> dict:
    """Append ONE event line. Append-only; never rewrites. Returns the event dict."""
    ts = _now_iso(iso_now)
    rec = {"ts": ts, "id": item_id, "event": event, **data}
    path = _events_file(ts)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def create_task(title: str, *, done_when: str | None = None, due: str | None = None,
                parent: str | None = None, est_minutes: int | None = None,
                tags: list[str] | None = None, links: list[str] | None = None,
                iso_now: str | None = None) -> dict:
    """Write one task file with a frozen id, log its birth, refresh the glance.

    Returns {id, path}. The id is content-addressed on (title, timestamp) so it is
    stable across renames/edits — identity never depends on the filename.
    """
    dirs = config.item_dirs()
    ts = _now_iso(iso_now)
    date = ts[:10]
    item_id = stable_id("item", "task", ts, title)
    fm: dict = {
        "id": item_id,
        "family": "task",
        "title": title,
        "created": date,
        "tags": list(tags or []),
        "links": list(links or []),
    }
    if done_when is not None:
        fm["done_when"] = done_when
    if parent is not None:
        fm["parent"] = parent
    if due is not None:
        fm["due"] = due
    if est_minutes is not None:
        fm["est_minutes"] = int(est_minutes)

    out = dirs["task"] / f"{date}-{_slug(title)}-{item_id[:8]}.md"
    front = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n"
    out.write_text(front, encoding="utf-8")

    append_event(item_id, "created", iso_now=ts, title=title)
    build_glance()
    return {"id": item_id, "path": _rel(out)}


def set_status(item_id: str, to: str, *, iso_now: str | None = None) -> dict:
    """Append a status transition (the authority for 'now') + refresh the glance."""
    if to not in STATUSES:
        raise ValueError(f"bad status {to!r}; want one of {STATUSES}")
    if item_id not in _tasks_by_id():
        raise KeyError(f"no task {item_id!r}")
    frm = fold_status(item_id)
    rec = append_event(item_id, "status", iso_now=iso_now, **{"from": frm, "to": to})
    build_glance()
    return rec


def done(item_id: str, *, iso_now: str | None = None) -> dict:
    """Sugar: mark a task done."""
    return set_status(item_id, "done", iso_now=iso_now)


# ── read / fold ───────────────────────────────────────────────────────────

def _read_events() -> list[dict]:
    """All events across every month shard, in (ts, event-order) order."""
    events_dir = config.item_dirs()["events"]
    out: list[dict] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        # errors="replace", matching every other reader in this file (the item
        # markdown readers below all do the same). A single bad byte in an
        # append-only log should cost that one line, not the entire nightly
        # rebuild — CI died on 0xa3 in one event and published nothing.
        #
        # Reported rather than swallowed: a tolerant read that says nothing is
        # how corruption becomes permanent. This does not rewrite the file.
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            text = raw.decode("utf-8", errors="replace")
            print(f"  WARN {path.name}: undecodable byte at {exc.start} "
                  f"({raw[exc.start:exc.start + 1]!r}) — line kept with U+FFFD, "
                  "file untouched")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # ts is the authoritative order; file order breaks ties within a timestamp.
    out.sort(key=lambda e: e.get("ts", ""))
    return out


def _split_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        fm = yaml.safe_load(text[3:end]) or {}
        return fm if isinstance(fm, dict) else {}
    except yaml.YAMLError:
        return {}


def _tasks_by_id() -> dict[str, dict]:
    """Every task file's authored frontmatter, keyed by frozen id."""
    task_dir = config.item_dirs()["task"]
    out: dict[str, dict] = {}
    for path in sorted(task_dir.glob("*.md")):
        fm = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        tid = fm.get("id")
        if tid:
            fm["_path"] = _rel(path)
            out[tid] = fm
    return out


def fold_status(item_id: str, events: list[dict] | None = None) -> str:
    """Current status = last status event's `to`, else 'todo'. The authority rule."""
    evs = events if events is not None else _read_events()
    status = "todo"
    for e in evs:
        if e.get("id") == item_id and e.get("event") == "status" and e.get("to") in STATUSES:
            status = e["to"]
    return status


def list_tasks(status: str | None = None) -> list[dict]:
    """Tasks with folded status. `status` filters; 'open' means todo|doing."""
    events = _read_events()
    tasks = _tasks_by_id()
    out: list[dict] = []
    for tid, fm in tasks.items():
        st = fold_status(tid, events)
        if status == "open":
            if st not in OPEN:
                continue
        elif status and st != status:
            continue
        out.append({
            "id": tid,
            "title": fm.get("title", ""),
            "status": st,
            "due": fm.get("due"),
            "done_when": fm.get("done_when"),
            "parent": fm.get("parent"),
            "est_minutes": fm.get("est_minutes"),
            "tags": fm.get("tags", []),
            "links": fm.get("links", []),
            "path": fm.get("_path"),
        })
    # Stable, useful order: open first, then by due date (undated last), then title.
    out.sort(key=lambda t: (t["status"] not in OPEN, t["due"] or "9999", t["title"]))
    return out


# ── glance (Kairos surface) ─────────────────────────────────────────────────

def build_glance() -> Path:
    """Write items/_glance/task.json — the projection Kairos reads (no DuckDB)."""
    dirs = config.item_dirs()
    payload = {
        "generatedAt": _now_iso()[:10],
        "tasks": list_tasks(),
    }
    out = dirs["glance"] / "task.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


# ── Type B: habits (ADR-0002) ────────────────────────────────────────────────
#
# A habit is an item with family=habit and NO terminal status — it is "active"
# forever (the sharpest boundary in the system). Completions are `done` events
# carrying a `date`; due-ness and streak are FOLDED from those dates + the cadence,
# never stored. This is habits/src/glance.ts (isDueOn/computeStreak) ported verbatim
# — the same authority rule, now on the spine. `seed_streak` in frontmatter is the
# migration fallback (the TS `done.size===0 -> stored streak` branch): honoured only
# until the habit's first logged completion, so migrating never wipes a streak.

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # Python weekday() order


def _d(s: str) -> _date:
    return _date.fromisoformat(s[:10])


def _week_start_mon(d: _date) -> _date:
    return d - timedelta(days=d.weekday())


def _abbrev_wd(abbrev: str) -> int:
    key = abbrev.strip().lower()[:3]
    if key not in _DAY_NAMES:
        raise ValueError(f"bad weekday abbrev: {abbrev}")
    return _DAY_NAMES.index(key)


def _weekly_target(spec: str) -> int | None:
    """The N in 'weekly' (=1) or 'Nx/week', else None."""
    if spec == "weekly":
        return 1
    m = re.match(r"^(\d+)x/week$", spec)
    return int(m.group(1)) if m else None


def _is_cadence_day(cadence, d: _date) -> bool:
    if isinstance(cadence, list):
        return d.weekday() in [_abbrev_wd(a) for a in cadence]
    return str(cadence).strip().lower() == "daily"


def is_due_on(cadence, d: _date, done: set[str]) -> bool:
    """Due on day `d`? daily=every day; array=named weekdays; weekly/Nx=until the
    week's quota is met (counting completions on/before `d`). Unknown -> not due."""
    if isinstance(cadence, list):
        return d.weekday() in [_abbrev_wd(a) for a in cadence]
    spec = str(cadence).strip().lower()
    if spec == "daily":
        return True
    target = _weekly_target(spec)
    if target is not None:
        start = _week_start_mon(d)
        so_far = sum(1 for i in range(7)
                     if (start + timedelta(days=i)) <= d and (start + timedelta(days=i)).isoformat() in done)
        return so_far < target
    return False


def compute_streak(cadence, done: set[str], as_of: _date, seed: int = 0) -> int:
    """Consecutive satisfied periods ending at `as_of`, resetting on a miss. Empty
    log -> `seed` (migration fallback). Ported from habits/glance.ts computeStreak."""
    if not done:
        return seed
    spec = "" if isinstance(cadence, list) else str(cadence).strip().lower()
    target = _weekly_target(spec)
    if target is not None:
        streak = 0
        wk = _week_start_mon(as_of)
        current = wk
        for _ in range(520):
            c = sum(1 for i in range(7) if (wk + timedelta(days=i)).isoformat() in done)
            if c >= target:
                streak += 1
            elif wk == current:
                pass  # current week short but in progress — grace
            else:
                break
            wk = wk - timedelta(days=7)
        return streak
    # day-based (daily or weekday array)
    streak = 0
    d = as_of
    for _ in range(3660):
        if _is_cadence_day(cadence, d):
            if d.isoformat() in done:
                streak += 1
            elif d == as_of:
                pass  # today due but not yet done — grace
            else:
                break
        d = d - timedelta(days=1)
    return streak


def _habits_by_id() -> dict[str, dict]:
    hdir = config.item_dirs()["habit"]
    out: dict[str, dict] = {}
    for path in sorted(hdir.glob("*.md")):
        fm = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        hid = fm.get("id")
        if hid:
            fm["_path"] = _rel(path)
            out[hid] = fm
    return out


def _done_dates(item_id: str, events: list[dict]) -> set[str]:
    return {e["date"] for e in events
            if e.get("id") == item_id and e.get("event") == "done" and e.get("date")}


def create_habit(title: str, cadence, *, block_minutes: int | None = None,
                 day_parts: list[str] | None = None, tags: list[str] | None = None,
                 links: list[str] | None = None, seed_streak: int | None = None,
                 item_id: str | None = None, iso_now: str | None = None) -> dict:
    """Write one habit file (family=habit, cadence authored) + log its birth.

    `item_id` preserves an existing identity (migration); else content-addressed.
    """
    dirs = config.item_dirs()
    ts = _now_iso(iso_now)
    date = ts[:10]
    hid = item_id or stable_id("item", "habit", ts, title)
    fm: dict = {
        "id": hid,
        "family": "habit",
        "title": title,
        "created": date,
        "cadence": cadence,
        "tags": list(tags or []),
        "links": list(links or []),
    }
    if block_minutes is not None:
        fm["block_minutes"] = int(block_minutes)
    if day_parts:
        fm["day_parts"] = list(day_parts)
    if seed_streak:
        fm["seed_streak"] = int(seed_streak)

    out = dirs["habit"] / f"{date}-{_slug(title)}-{hid[:8]}.md"
    out.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n",
                   encoding="utf-8")
    append_event(hid, "created", iso_now=ts, title=title)
    build_habit_glance()
    return {"id": hid, "path": _rel(out)}


def complete_habit(item_id: str, date: str | None = None, *, iso_now: str | None = None) -> dict:
    """Log a completion (idempotent per id+date, like habits appendLog). No status
    ever changes — Type B has no terminal done. Refreshes the habit glance."""
    if item_id not in _habits_by_id():
        raise KeyError(f"no habit {item_id!r}")
    day = (date or _now_iso(iso_now))[:10]
    for e in _read_events():  # dedup: one completion per day
        if e.get("id") == item_id and e.get("event") == "done" and e.get("date") == day:
            return e
    rec = append_event(item_id, "done", iso_now=iso_now, date=day)
    build_habit_glance()
    return rec


def list_habits(as_of: str | None = None) -> list[dict]:
    """Habits with folded streak (as of today)."""
    events = _read_events()
    ref = _d(as_of or _now_iso()[:10])
    out = []
    for hid, fm in _habits_by_id().items():
        done = _done_dates(hid, events)
        out.append({
            "id": hid,
            "title": fm.get("title", ""),
            "cadence": fm.get("cadence"),
            "streak": compute_streak(fm.get("cadence"), done, ref, int(fm.get("seed_streak") or 0)),
            "block_minutes": fm.get("block_minutes"),
            "day_parts": fm.get("day_parts"),
            "path": fm.get("_path"),
        })
    out.sort(key=lambda h: h["title"])
    return out


def build_habit_glance(from_date: str | None = None, horizon: int = 7) -> Path:
    """Write items/_glance/habit.json in Kairos's week-shape (Type B rail). Same
    fields habits/glance.ts emitted, so Kairos reads one file, unchanged in shape."""
    dirs = config.item_dirs()
    frm = from_date or _now_iso()[:10]
    start = _d(frm)
    events = _read_events()
    habits = _habits_by_id()
    done_by = {hid: _done_dates(hid, events) for hid in habits}
    streak_of = {hid: compute_streak(fm.get("cadence"), done_by[hid], start, int(fm.get("seed_streak") or 0))
                 for hid, fm in habits.items()}
    week = []
    for i in range(horizon):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        day_habits = []
        for hid, fm in habits.items():
            if is_due_on(fm.get("cadence"), d, done_by[hid]):
                gh = {
                    "id": hid,
                    "title": fm.get("title", ""),
                    "cadence": fm.get("cadence"),
                    "dueToday": True,
                    "streak": streak_of[hid],
                }
                if fm.get("block_minutes") is not None:
                    gh["blockMinutes"] = fm["block_minutes"]
                if fm.get("day_parts"):
                    gh["dayParts"] = fm["day_parts"]
                day_habits.append(gh)
        week.append({"date": ds, "day": _DAY_NAMES[d.weekday()], "isToday": ds == frm, "habits": day_habits})
    out = dirs["glance"] / "habit.json"
    out.write_text(json.dumps({"generatedAt": frm, "week": week}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    return out


# ── Type C: slot-fill (ADR-0002) ─────────────────────────────────────────────
#
# One taste-ranked slot-fill core; swap the pile + constraint set (ADR-0001):
# slate = media backlog, meals = recipes, events = the dc-events pool. The RANKER
# is taste-engine buildPlan — it already runs in Kairos rail 1 (`taste plan --json`)
# and hard-filters by constraint. It is NOT rebuilt here.
#
# The spine's Type C contribution is the item representation the ranker was missing:
#   slot        — an item to fill from a pile under constraints (family=slot).
#   constraint  — a standing HARD filter, e.g. an allergy-rule (family=constraint).
#                 allergy is a filter, not a taste weight (dining-vertical-storage).
#   pick event  — fills a slot with a chosen pile member. A pick IS the meal-log /
#                 consumed record (ADR-0002): family=slot filled by kind=recipe.
# Slot status folds open -> filled from pick events (the same authority rule).


def create_slot(title: str, pile: str, *, slot_date: str | None = None,
                constraints: list[str] | None = None, block_minutes: int | None = None,
                day_parts: list[str] | None = None, tags: list[str] | None = None,
                links: list[str] | None = None, item_id: str | None = None,
                iso_now: str | None = None) -> dict:
    """Create a slot to fill from `pile` under `constraints` (hard filters)."""
    dirs = config.item_dirs()
    ts = _now_iso(iso_now)
    date = ts[:10]
    sid = item_id or stable_id("item", "slot", ts, title)
    fm: dict = {
        "id": sid,
        "family": "slot",
        "title": title,
        "created": date,
        "pile": pile,
        "constraints": list(constraints or []),
        "tags": list(tags or []),
        "links": list(links or []),
    }
    if slot_date is not None:
        fm["slot_date"] = slot_date
    if block_minutes is not None:
        fm["block_minutes"] = int(block_minutes)
    if day_parts:
        fm["day_parts"] = list(day_parts)

    out = dirs["slot"] / f"{date}-{_slug(title)}-{sid[:8]}.md"
    out.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n",
                   encoding="utf-8")
    append_event(sid, "created", iso_now=ts, title=title)
    build_slot_glance()
    return {"id": sid, "path": _rel(out)}


def create_constraint(rule: str, *, severity: str = "hard", scope: str = "*",
                      title: str | None = None, tags: list[str] | None = None,
                      links: list[str] | None = None, item_id: str | None = None,
                      iso_now: str | None = None) -> dict:
    """Create a standing constraint (e.g. an allergy-rule) that hard-filters a pile.
    `scope` names the pile it applies to ('*' = every pile)."""
    dirs = config.item_dirs()
    ts = _now_iso(iso_now)
    date = ts[:10]
    cid = item_id or stable_id("item", "constraint", rule, scope)
    fm: dict = {
        "id": cid,
        "family": "constraint",
        "title": title or rule,
        "created": date,
        "rule": rule,
        "severity": severity,
        "scope": scope,
        "tags": list(tags or []),
        "links": list(links or []),
    }
    out = dirs["constraint"] / f"{date}-{_slug(rule)}-{cid[:8]}.md"
    out.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n",
                   encoding="utf-8")
    append_event(cid, "created", iso_now=ts, title=fm["title"])
    build_slot_glance()
    return {"id": cid, "path": _rel(out)}


def fill_slot(slot_id: str, ref: str, *, ref_kind: str = "recipe",
              date: str | None = None, iso_now: str | None = None) -> dict:
    """Fill a slot with a chosen pile member — a `pick` event, which IS the
    meal-log / consumed record. Slot status folds to 'filled'. Repeated picks are
    kept (history); the latest is `filled_with`."""
    if slot_id not in _slots_by_id():
        raise KeyError(f"no slot {slot_id!r}")
    day = (date or _now_iso(iso_now))[:10]
    rec = append_event(slot_id, "pick", iso_now=iso_now, date=day, ref=ref, ref_kind=ref_kind)
    build_slot_glance()
    return rec


def _slots_by_id() -> dict[str, dict]:
    sdir = config.item_dirs()["slot"]
    out: dict[str, dict] = {}
    for path in sorted(sdir.glob("*.md")):
        fm = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        sid = fm.get("id")
        if sid:
            fm["_path"] = _rel(path)
            out[sid] = fm
    return out


def _constraints_by_id() -> dict[str, dict]:
    cdir = config.item_dirs()["constraint"]
    out: dict[str, dict] = {}
    for path in sorted(cdir.glob("*.md")):
        fm = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        cid = fm.get("id")
        if cid:
            fm["_path"] = _rel(path)
            out[cid] = fm
    return out


def _picks(slot_id: str, events: list[dict]) -> list[dict]:
    """Pick events for a slot, in order (each is a meal-log/consumed record)."""
    return [e for e in events if e.get("id") == slot_id and e.get("event") == "pick"]


def fold_slot_status(slot_id: str, events: list[dict] | None = None) -> str:
    """A slot is 'filled' once any pick exists, else 'open'. The authority rule."""
    evs = events if events is not None else _read_events()
    return "filled" if _picks(slot_id, evs) else "open"


def filled_with(slot_id: str, events: list[dict] | None = None) -> str | None:
    """The latest pick's ref for a slot, or None."""
    evs = events if events is not None else _read_events()
    picks = _picks(slot_id, evs)
    return picks[-1].get("ref") if picks else None


def active_constraints(pile: str | None = None) -> list[dict]:
    """Standing constraints applying to `pile` (scope match, '*' = all). The hook a
    ranker reads to hard-filter a pile — allergy as a filter, not a taste weight."""
    out = []
    for cid, fm in _constraints_by_id().items():
        scope = str(fm.get("scope") or "*")
        if pile is None or scope == "*" or scope == pile:
            out.append({"id": cid, "rule": fm.get("rule"), "severity": fm.get("severity", "hard"),
                        "scope": scope, "title": fm.get("title")})
    return out


def list_slots(status: str | None = None) -> list[dict]:
    """Slots with folded status ('open'|'filled'). `status` filters."""
    events = _read_events()
    out = []
    for sid, fm in _slots_by_id().items():
        st = fold_slot_status(sid, events)
        if status and st != status:
            continue
        out.append({
            "id": sid,
            "title": fm.get("title", ""),
            "status": st,
            "pile": fm.get("pile"),
            "constraints": fm.get("constraints", []),
            "slot_date": fm.get("slot_date"),
            "block_minutes": fm.get("block_minutes"),
            "day_parts": fm.get("day_parts"),
            "filled_with": filled_with(sid, events),
            "path": fm.get("_path"),
        })
    out.sort(key=lambda s: (s["status"] != "open", s["slot_date"] or "9999", s["title"]))
    return out


def build_slot_glance() -> Path:
    """Write items/_glance/slot.json — open slots (Kairos day rail) + active
    constraints + recent picks (the meal-log tail)."""
    dirs = config.item_dirs()
    events = _read_events()
    picks = [e for e in events if e.get("event") == "pick"]
    payload = {
        "generatedAt": _now_iso()[:10],
        "slots": list_slots(),
        "constraints": active_constraints(),
        "picks": [{"slot": e.get("id"), "ref": e.get("ref"), "ref_kind": e.get("ref_kind"),
                   "date": e.get("date")} for e in picks[-20:]],
    }
    out = dirs["glance"] / "slot.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out
