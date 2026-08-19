"""The item spine (ADR-0002), Type A tasks: authored noun + folded event log."""
from __future__ import annotations

import json
from datetime import date as _date

import pytest

from exo import config, item
from exo.loaders import t1_index


@pytest.fixture
def items_dir(tmp_path, monkeypatch):
    """Point the spine at a throwaway items/ so tests never touch the real store."""
    monkeypatch.setattr(config, "ITEMS", tmp_path / "items")
    return tmp_path / "items"


def test_create_writes_file_with_frozen_id_and_logs_birth(items_dir):
    res = item.create_task("Ship the spine", due="2026-08-15", done_when="ADR merged",
                           tags=["personal-os"], iso_now="2026-08-10T09:00:00+00:00")
    assert res["id"] and res["path"].endswith(".md") and "task" in res["path"]
    md = (items_dir / "task").glob("*.md").__next__().read_text()
    fm = item._split_frontmatter(md)
    assert fm["id"] == res["id"]           # id frozen into the file
    assert fm["title"] == "Ship the spine"
    assert fm["due"] == "2026-08-15"
    assert "status" not in fm              # authority rule: status is NOT on the noun
    evs = item._read_events()
    assert [e["event"] for e in evs] == ["created"]
    assert evs[0]["id"] == res["id"]


def test_id_is_stable_across_calls_but_unique_per_task(items_dir):
    a = item.create_task("Same title", iso_now="2026-08-10T09:00:00+00:00")["id"]
    # same title, DIFFERENT timestamp -> different task (distinct id + file)
    b = item.create_task("Same title", iso_now="2026-08-10T10:00:00+00:00")["id"]
    assert a != b
    assert len(list((items_dir / "task").glob("*.md"))) == 2


def test_status_folds_from_the_log_last_wins(items_dir):
    tid = item.create_task("t", iso_now="2026-08-10T09:00:00+00:00")["id"]
    assert item.fold_status(tid) == "todo"          # default, no status event
    item.set_status(tid, "doing", iso_now="2026-08-10T10:00:00+00:00")
    item.set_status(tid, "done", iso_now="2026-08-10T11:00:00+00:00")
    assert item.fold_status(tid) == "done"          # last status event wins


def test_status_events_carry_from_and_to(items_dir):
    tid = item.create_task("t", iso_now="2026-08-10T09:00:00+00:00")["id"]
    item.set_status(tid, "doing", iso_now="2026-08-10T10:00:00+00:00")
    rec = item.done(tid, iso_now="2026-08-10T11:00:00+00:00")
    assert rec["from"] == "doing" and rec["to"] == "done"


def test_set_status_validates(items_dir):
    tid = item.create_task("t", iso_now="2026-08-10T09:00:00+00:00")["id"]
    with pytest.raises(ValueError):
        item.set_status(tid, "bogus")
    with pytest.raises(KeyError):
        item.set_status("nope", "done")


def test_list_filters_open_vs_done(items_dir):
    a = item.create_task("open one", iso_now="2026-08-10T09:00:00+00:00")["id"]
    b = item.create_task("done one", iso_now="2026-08-10T09:01:00+00:00")["id"]
    item.done(b, iso_now="2026-08-10T12:00:00+00:00")
    ids_open = {t["id"] for t in item.list_tasks("open")}
    ids_done = {t["id"] for t in item.list_tasks("done")}
    assert ids_open == {a} and ids_done == {b}
    assert len(item.list_tasks()) == 2


def test_glance_reflects_folded_status(items_dir):
    tid = item.create_task("g", due="2026-08-12", iso_now="2026-08-10T09:00:00+00:00")["id"]
    item.set_status(tid, "doing", iso_now="2026-08-10T10:00:00+00:00")
    g = json.loads((items_dir / "_glance" / "task.json").read_text())
    row = next(t for t in g["tasks"] if t["id"] == tid)
    assert row["status"] == "doing" and row["due"] == "2026-08-12"


def test_loader_projects_folded_status_and_is_deterministic(items_dir):
    tid = item.create_task("indexed", iso_now="2026-08-10T09:00:00+00:00")["id"]
    item.done(tid, iso_now="2026-08-10T12:00:00+00:00")
    rows1 = t1_index.items()
    rows2 = t1_index.items()
    assert [r.id for r in rows1] == [r.id for r in rows2]           # idempotent
    r = next(r for r in rows1 if r.id == tid)
    assert r.tier == "t1" and r.zone == "item" and r.grounds is True
    assert r.payload["status"] == "done"                            # fold, not stored
    assert json.loads(r.payload["tags"]) == []
    evrows = t1_index.item_events()
    assert {e.payload["event"] for e in evrows} == {"created", "status"}
    assert {e.payload["to_status"] for e in evrows if e.payload["event"]=="status"}
    assert all(e.zone == "item_event" and e.tier == "t1" for e in evrows)


# ── Type B: habits ───────────────────────────────────────────────────────────

def test_create_habit_has_no_status_and_carries_cadence(items_dir):
    res = item.create_habit("Journal", "daily", block_minutes=15, day_parts=["evening"],
                            iso_now="2026-08-10T09:00:00+00:00")
    fm = item._split_frontmatter((items_dir / "habit").glob("*.md").__next__().read_text())
    assert fm["family"] == "habit" and fm["cadence"] == "daily"
    assert "status" not in fm                      # Type B never terminates
    assert item.list_habits()[0]["id"] == res["id"]


def test_completion_is_idempotent_per_day(items_dir):
    hid = item.create_habit("H", "daily", iso_now="2026-08-10T09:00:00+00:00")["id"]
    item.complete_habit(hid, "2026-08-10")
    item.complete_habit(hid, "2026-08-10")        # same day again -> no-op
    dones = [e for e in item._read_events() if e.get("event") == "done"]
    assert len(dones) == 1


def test_seed_streak_is_fallback_until_first_completion(items_dir):
    hid = item.create_habit("H", "daily", seed_streak=6, iso_now="2026-08-10T09:00:00+00:00")["id"]
    # empty completion log -> seed shows through
    assert item.list_habits(as_of="2026-08-10")[0]["streak"] == 6
    # once real completions exist, they win over the seed
    for day in ("2026-08-08", "2026-08-09", "2026-08-10"):
        item.complete_habit(hid, day)
    assert item.list_habits(as_of="2026-08-10")[0]["streak"] == 3


def test_is_due_on_nx_week_from_log(items_dir):
    # 3x/week: due until 3 completions logged in the week (Mon 8/10..)
    done = {"2026-08-10", "2026-08-11"}
    assert item.is_due_on("3x/week", item._d("2026-08-12"), done) is True   # owe 1 more
    done3 = done | {"2026-08-12"}
    assert item.is_due_on("3x/week", item._d("2026-08-13"), done3) is False  # quota met


def test_compute_streak_resets_on_miss(items_dir):
    done = {"2026-08-08", "2026-08-10"}           # missed the 9th
    assert item.compute_streak("daily", done, item._d("2026-08-10")) == 1


def test_habit_glance_shape_and_loader(items_dir):
    hid = item.create_habit("Lift", ["mon", "wed", "fri"], block_minutes=60,
                            seed_streak=11, iso_now="2026-08-10T09:00:00+00:00")["id"]
    # Pin the window. create_habit writes the glance from the REAL today, not
    # from its iso_now — rightly, since the glance is a view of now — so a test
    # asserting on a fixed Monday only passed during the week that contained it.
    item.build_habit_glance(from_date="2026-08-10")
    g = json.loads((items_dir / "_glance" / "habit.json").read_text())
    mon = next(d for d in g["week"] if d["date"] == "2026-08-10")   # Monday -> due
    row = next(h for h in mon["habits"] if h["id"] == hid)
    assert row["streak"] == 11 and row["blockMinutes"] == 60 and row["dueToday"] is True
    # loader projects habits into t1_item with status=active + folded streak
    r = next(r for r in t1_index.items() if r.id == hid)
    assert r.payload["family"] == "habit" and r.payload["status"] == "active"
    assert r.payload["streak"] == "11"


def test_habit_glance_window_covers_today(items_dir):
    """The habit glance is the one projection that WINDOWS on now, so it is only
    correct while it is fresh — and nothing rebuilt it on a schedule. In
    production items/_glance/habit.json sat nine days old, covering a week that
    had already ended, with isToday marking a Monday long past and no entry for
    today at all. Kairos reads that file. This asserts the freshly built window
    contains today, on whatever day it runs."""
    item.create_habit("Lift", "daily", iso_now="2026-08-10T09:00:00+00:00")
    item.build_habit_glance()
    g = json.loads((items_dir / "_glance" / "habit.json").read_text())
    today = _date.today().isoformat()
    assert g["generatedAt"] == today
    assert any(d["date"] == today and d["isToday"] for d in g["week"])


# ── Type C: slot-fill ────────────────────────────────────────────────────────

def test_slot_folds_open_then_filled(items_dir):
    sid = item.create_slot("Dinner tonight", "recipes", slot_date="2026-08-10",
                           constraints=["allergy:nut-free"], iso_now="2026-08-10T09:00:00+00:00")["id"]
    assert item.fold_slot_status(sid) == "open"
    assert item.filled_with(sid) is None
    rec = item.fill_slot(sid, "recipe:miso-cod", date="2026-08-10")
    assert rec["event"] == "pick" and rec["ref"] == "recipe:miso-cod"  # the meal-log entry
    assert item.fold_slot_status(sid) == "filled"
    assert item.filled_with(sid) == "recipe:miso-cod"


def test_fill_unknown_slot_raises(items_dir):
    with pytest.raises(KeyError):
        item.fill_slot("nope", "recipe:x")


def test_constraints_hard_filter_hook_by_scope(items_dir):
    item.create_constraint("nut-free", scope="recipes")
    item.create_constraint("no-spoilers", scope="media")
    recipes = {c["rule"] for c in item.active_constraints("recipes")}
    assert "nut-free" in recipes and "no-spoilers" not in recipes  # scope-matched
    # '*' scope applies everywhere
    item.create_constraint("halal", scope="*")
    assert "halal" in {c["rule"] for c in item.active_constraints("media")}


def test_slot_glance_lists_open_slots_and_picks(items_dir):
    sid = item.create_slot("Sat dinner", "recipes", slot_date="2026-08-15",
                           iso_now="2026-08-10T09:00:00+00:00")["id"]
    item.create_slot("Filled one", "recipes", iso_now="2026-08-10T09:01:00+00:00")
    item.fill_slot(item.list_slots()[-1]["id"] if False else
                   next(s["id"] for s in item.list_slots() if s["title"] == "Filled one"),
                   "recipe:tagine", date="2026-08-10")
    g = json.loads((items_dir / "_glance" / "slot.json").read_text())
    open_titles = {s["title"] for s in g["slots"] if s["status"] == "open"}
    assert "Sat dinner" in open_titles
    assert any(p["ref"] == "recipe:tagine" for p in g["picks"])  # meal-log tail
    assert next(s for s in g["slots"] if s["id"] == sid)["constraints"] == []


def test_loader_projects_slot_and_constraint(items_dir):
    sid = item.create_slot("D", "recipes", constraints=["allergy:nut-free"],
                           iso_now="2026-08-10T09:00:00+00:00")["id"]
    item.fill_slot(sid, "recipe:x", date="2026-08-10")
    cid = item.create_constraint("nut-free", scope="recipes")["id"]
    rows = {r.id: r for r in t1_index.items()}
    assert rows[sid].payload["family"] == "slot" and rows[sid].payload["status"] == "filled"
    assert rows[sid].payload["filled_with"] == "recipe:x"
    assert json.loads(rows[sid].payload["constraints"]) == ["allergy:nut-free"]
    assert rows[cid].payload["family"] == "constraint" and rows[cid].payload["rule"] == "nut-free"
    # pick event carries the meal-log fields
    ev = next(e for e in t1_index.item_events() if e.payload["event"] == "pick")
    assert ev.payload["ref"] == "recipe:x" and ev.payload["date"] == "2026-08-10"
