"""T1 registration — index second-brain, DON'T move it (ADR-0001, Fig 5 Ph3).

second-brain's markdown stays the source of truth: human-edited, under git,
never written by Exo. Here we only READ it and materialize a
queryable projection into zones/t1. Losing these parquets costs nothing —
`wh index` rebuilds them from the vault.

Four zones:
  note        raw/ + refined/ markdown (frontmatter + body)
  verdict     media-verdicts.json (authored ratings/opinions)
  visit       restaurant-visits.csv (rated dining ground truth)
  open_thread open-questions/ (human questions found in raw; has state)

All author=human, grounds=True (derivation MAY read T1 as source), tier=t1.
`regenerable` stays False: these are projections of an external record (the
vault), not output derived from a lower tier — that flag is T2's alone.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import config, io, plugins
from ..provenance import Row, stable_id

_NOTE_DIRS = ["raw", "refined"]


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end]
    body = text[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(block) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def _mtime_iso(path: Path) -> str:
    """File mtime as a UTC ISO string — a recency key for zones whose source markdown
    carries no authored `created` (open-questions). Sorts lexically = chronologically,
    matching the VARCHAR `created` convention, so downstream ORDER BY created works."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _s(v) -> str:
    """Coerce a frontmatter scalar (date, int, list) to a stable string."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def notes() -> list[Row]:
    base = config.VAULT
    rows: list[Row] = []
    for sub in _NOTE_DIRS:
        for path in io.markdown(base / sub, recursive=True):
            text = path.read_text(encoding="utf-8", errors="replace")
            fm, body = _split_frontmatter(text)
            rel = str(path.relative_to(base))
            rows.append(Row(
                tier="t1", zone="note", source="second-brain", author="human",
                created=_s(fm.get("created")) or None, origin_ref=rel,
                payload={
                    "title": _s(fm.get("title")) or path.stem,
                    "note_type": _s(fm.get("type")) or sub,
                    "voice": _s(fm.get("voice")),
                    "facet": _s(fm.get("facet")),
                    "folder": _s(fm.get("folder")),
                    "body": body,
                },
            ))
    return rows


def verdicts() -> list[Row]:
    path = config.VAULT / "media-verdicts.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    rows: list[Row] = []
    for key, v in data.items():
        if not isinstance(v, dict):
            continue
        rows.append(Row(
            tier="t1", zone="verdict", source="second-brain", author="human",
            origin_ref=key,
            payload={
                "subject": _s(v.get("title")),
                "kind": _s(v.get("source")),
                "year": _s(v.get("year")),
                "rating": _s(v.get("rating")),
                "note": _s(v.get("draft")),
            },
        ))
    return rows


def film_reviews() -> list[Row]:
    """Written Letterboxd reviews — T1, authored words, same category as verdicts.

    The reviews export carries a `Review` column the film loader never read, so
    115 pieces of that criticism sat on disk while an assistant had only the
    10 media-verdicts to judge how the owner thinks about film.

    Kept apart from t0_film rather than folded into it: t0_film is what
    Letterboxd recorded (author=external), a review is what the owner wrote
    (author=human, grounds=True). Same distinction verdicts already draw.

    The RSS cache has no review field, so unlike ratings these only refresh on a
    fresh export — the newest here is dated 2026-03-25.
    """
    path = config.latest("letterboxd_reviews*.csv")
    if not path:
        return []
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            text = (rec.get("Review") or "").strip()
            if not text:
                continue
            rows.append(Row(
                tier="t1", zone="film_review", source="letterboxd", author="human",
                created=(rec.get("Watched Date") or rec.get("Date") or None),
                origin_ref=rec.get("Letterboxd URI", ""),
                payload={
                    "title": rec.get("Name", ""),
                    "year": rec.get("Year", ""),
                    "rating": rec.get("Rating", ""),
                    "rewatch": (rec.get("Rewatch") or "").strip().lower() in ("yes", "true", "1"),
                    "review": text,
                    "url": rec.get("Letterboxd URI", ""),
                },
            ))
    return rows


def visits() -> list[Row]:
    """Faithful projection — carries EVERY CSV column so a consumer (dining +
    travel) can reconstruct its Visit without touching the file. Dropping
    columns here would make the zone lossy and block cutover."""
    path = config.VAULT / "restaurant-visits.csv"
    if not path.exists():
        return []
    cols = ["rank", "price", "enjoyed", "era", "occasion", "cuisine_1", "cuisine_2",
            "cuisine_3", "neighborhood", "city", "state", "notes"]
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            payload = {"restaurant": rec.get("restaurant", ""), "rating": rec.get("rating", "")}
            payload.update({c: rec.get(c, "") for c in cols})
            rows.append(Row(
                tier="t1", zone="visit", source="second-brain", author="human",
                created=(rec.get("date_visited") or None), origin_ref=rec.get("restaurant", ""),
                payload=payload,
            ))
    return rows


def recipes() -> list[Row]:
    """recipes.json — authored cook-at-home candidates (ADR-0003, "T1's second
    form": authored structured data, not vault markdown). Read here, materialized
    into zones/t1/recipe; the taste-engine cooking vertical ranks off this.

    List-valued fields (ingredients, steps, tags) are stored as JSON strings so
    the column stays uniform and a consumer can `json.parse` them back. `id` is
    stable on recipe_id so editing a recipe's steps re-projects idempotently.
    """
    path = config.VAULT / "recipes.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    rows: list[Row] = []
    for r in data:
        rid = r.get("recipe_id")
        rows.append(Row(
            tier="t1", zone="recipe", source="second-brain", author="human",
            origin_ref=str(rid), id=stable_id("recipes", rid),
            payload={
                "recipe_id": rid,
                "base": _s(r.get("base")),
                "cuisine": _s(r.get("cuisine")),
                "flavor_group": _s(r.get("flavor_group")),
                "starch_base": _s(r.get("starch_base")),
                "sauce_mode": _s(r.get("sauce_mode")),
                "time_min": int(r.get("time_min") or 0),
                "effort": int(r.get("effort") or 0),
                "yield_servings": int(r.get("yield_servings") or 0),
                "allergy_safe_nut_free": bool(r.get("allergy_safe_nut_free")),
                "tested": bool(r.get("tested")),
                "sauce_ingredients": json.dumps(r.get("sauce_ingredients") or []),
                "toppings": json.dumps(r.get("toppings") or []),
                "pantry_needs": json.dumps(r.get("pantry_needs") or []),
                "make_ahead": json.dumps(r.get("make_ahead") or []),
                "steps": json.dumps(r.get("steps") or []),
                "tags": json.dumps(r.get("tags") or []),
                # ADR-0003 amendment: recipes now carry provenance. The seed is a
                # synthetic template — no title, no source URL, flat-ingredient
                # list unused (it decomposes into sauce/toppings), is_seed=True.
                "title": "",
                "ingredients": json.dumps([]),
                "source": "seed",
                "source_url": "",
                "is_seed": True,
            },
        ))
    return rows


# Headings whose bullets are commentary, not ingredients (cocktail "Notes",
# "Substitution Ideas", recipe "Inspiration"). Steps are captured by number, so
# this only gates the ingredient list.
_SKIP_HEADING = re.compile(r"note|substitut|tip|variation|inspiration", re.I)


def _recipe_lists(body: str) -> tuple[list[str], list[str]]:
    """Split a blog recipe body into (ingredients, steps).

    Recipe posts write ingredients as bullets (`-`/`*`) under an "Ingredients"
    heading — sometimes several (Spam Chorizo / Quesadilla / Crema), so we take
    every bullet, not one section — and steps as a numbered list. Heading styles
    vary (`Ingredients:`, `**Ingredients:**`, `## Ingredients`); a heading that
    reads as commentary (Notes/Substitutions) turns off ingredient capture until
    the next heading. Steps are captured by their number regardless.
    """
    ings: list[str] = []
    steps: list[str] = []
    skip = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        is_heading = bool(re.match(r"^(#{1,6}\s+|\*\*)", line)) or (
            line.endswith(":") and line[0] not in "-*0123456789"
        )
        if is_heading:
            skip = bool(_SKIP_HEADING.search(line))
            continue
        mb = re.match(r"^[-*]\s+(.*)", line)
        mn = re.match(r"^\d+\.\s+(.*)", line)
        if mb and not skip:
            ings.append(mb.group(1).strip())
        elif mn:
            steps.append(mn.group(1).strip())
    return ings, steps


def recipes_blog() -> list[Row]:
    """Real recipes from the blog — the mirrored blog posts, frontmatter
    `type: recipe` (ADR-0003 amendment, "T1's third form": authored markdown that
    IS a recipe, distinct from the synthetic seed and from vault notes).

    A blog recipe is published, so `tested=True`, and it cites its post URL —
    the taste-engine cooking vertical boosts real+tested recipes over templates.
    Bowl-shape fields (base/starch_base/sauce_mode) stay empty; the ingredient
    list is flat, and `flavor_group` is the cuisine (each post its own group, so
    the variety penalty still has an axis). recipe_id is 1000+ to never collide
    with the 1–50 seed; assigned by sorted filename so re-index is stable.
    """
    posts = config.POSTS
    if not posts.exists():
        return []
    rows: list[Row] = []
    rid = 1000
    for path in io.markdown(posts):
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if str(fm.get("type", "")).strip() != "recipe":
            continue
        rid += 1
        ings, steps = _recipe_lists(body)
        cuisine = _s(fm.get("cuisine"))
        slug = _s(fm.get("slug")) or path.stem
        tags = [_s(t) for t in (fm.get("tags") or []) if not str(t).isdigit()]
        rows.append(Row(
            tier="t1", zone="recipe", source="blog", author="human",
            created=(_s(fm.get("publish_datetime")) or None),
            origin_ref=slug, id=stable_id("recipes_blog", slug),
            payload={
                "recipe_id": rid,
                "title": _s(fm.get("title")),
                "base": "",
                "cuisine": cuisine,
                "flavor_group": cuisine,
                "starch_base": "",
                "sauce_mode": "",
                "time_min": int(fm.get("time") or 0),
                "effort": int(fm.get("effort") or 0),
                "yield_servings": int(fm.get("yield") or 0),
                "allergy_safe_nut_free": False,
                "tested": True,
                "sauce_ingredients": json.dumps([]),
                "toppings": json.dumps([]),
                "pantry_needs": json.dumps([]),
                "make_ahead": json.dumps([]),
                "steps": json.dumps(steps),
                "tags": json.dumps(tags),
                "ingredients": json.dumps(ings),
                "source": "blog",
                "source_url": config.post_url(slug),
                "is_seed": False,
            },
        ))
    return rows


def open_threads() -> list[Row]:
    """open-questions/ — human questions found in raw (`type: open-thread`).

    A human-structured T1 zone, authored in the vault and only mirrored here:
    projected + queryable (what's open, joinable to its source note via
    `note_source`) but NOT atomized — a question is a pointer with state, not
    corpus prose. `state` (open/closed) is hand-edited in the markdown, so this
    stays a faithful projection. Keeps the vault's own `id` so re-projection is
    idempotent.
    """
    base = config.VAULT / "open-questions"
    if not base.exists():
        return []
    rows: list[Row] = []
    for path in io.markdown(base, recursive=True):
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = _split_frontmatter(text)
        rows.append(Row(
            tier="t1", zone="open_thread", source="second-brain", author="human",
            # OQ markdown has no authored `created`; fall back to file mtime so the zone
            # carries a recency key (life-terminal openThreads ORDER BY created — B1).
            created=_s(fm.get("created")) or _mtime_iso(path),
            origin_ref=str(path.relative_to(config.VAULT)),
            id=_s(fm.get("id")),  # keep the vault's id; Row hashes payload if empty
            payload={
                "question": body.strip(),
                "note_source": _s(fm.get("source")).strip("[]"),  # [[stem]] -> stem
                "state": _s(fm.get("state")) or "open",
                "origin": _s(fm.get("origin")),
            },
        ))
    return rows


def items() -> list[Row]:
    """The item spine (ADR-0002): authored .md nouns across families, folded state.

    Status/streak are never stored on the file — they are the fold of
    `items/_events/*.jsonl`. We compute them here so `t1_item` carries the true
    current state without a window function. Type A tasks fold a status; Type B
    habits are "active" forever and fold a streak. Lists are JSON-encoded.
    """
    from .. import item as item_mod

    events = item_mod._read_events()
    today = item_mod._d(item_mod._now_iso()[:10])
    rows: list[Row] = []
    for fam in ("task", "habit", "slot", "constraint"):
        for path in io.markdown(config.ITEMS / fam):
            fm = item_mod._split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            tid = fm.get("id")
            if not tid:
                continue
            _, body = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            streak = ""
            if fam == "task":
                status = item_mod.fold_status(str(tid), events)
            elif fam == "habit":
                status = "active"  # Type B never terminates
                streak = _s(item_mod.compute_streak(
                    fm.get("cadence"), item_mod._done_dates(str(tid), events),
                    today, int(fm.get("seed_streak") or 0)))
            elif fam == "slot":
                status = item_mod.fold_slot_status(str(tid), events)  # open|filled
            else:  # constraint — standing
                status = "active"
            rows.append(Row(
                tier="t1", zone="item", source="items", author="human",
                created=_s(fm.get("created")) or None,
                origin_ref=str(path.relative_to(config.ITEMS)),
                id=str(tid),
                payload={
                    "family": _s(fm.get("family")) or fam,
                    "title": _s(fm.get("title")) or path.stem,
                    "status": status,
                    "streak": streak,
                    "cadence": _s(fm.get("cadence")),
                    "done_when": _s(fm.get("done_when")),
                    "parent": _s(fm.get("parent")),
                    "due": _s(fm.get("due")),
                    "est_minutes": _s(fm.get("est_minutes")),
                    "block_minutes": _s(fm.get("block_minutes")),
                    "day_parts": json.dumps(fm.get("day_parts") or []),
                    # Type C fields
                    "pile": _s(fm.get("pile")),
                    "constraints": json.dumps(fm.get("constraints") or []),
                    "slot_date": _s(fm.get("slot_date")),
                    "filled_with": _s(item_mod.filled_with(str(tid), events)) if fam == "slot" else "",
                    "rule": _s(fm.get("rule")),
                    "severity": _s(fm.get("severity")),
                    "scope": _s(fm.get("scope")),
                    "tags": json.dumps(fm.get("tags") or []),
                    "links": json.dumps(fm.get("links") or []),
                    "body": body,
                },
            ))
    return rows


def item_events() -> list[Row]:
    """The append-only item event log -> t1_item_event (one row per event line)."""
    from .. import item as item_mod

    rows: list[Row] = []
    for e in item_mod._read_events():
        ts = _s(e.get("ts"))
        rows.append(Row(
            tier="t1", zone="item_event", source="items", author="human",
            created=ts or None,
            origin_ref=f"_events/{ts[:7]}.jsonl" if ts else "_events",
            id=stable_id("item_event", e.get("id"), e.get("event"), ts, e.get("to"), e.get("ref"), e.get("date")),
            payload={
                "item_id": _s(e.get("id")),
                "event": _s(e.get("event")),
                "ts": ts,
                "from_": _s(e.get("from")),
                "to_status": _s(e.get("to")),
                "date": _s(e.get("date")),        # done (habit) + pick (slot) completions
                "ref": _s(e.get("ref")),          # pick target (the meal-log entry)
                "ref_kind": _s(e.get("ref_kind")),
                "title": _s(e.get("title")),
            },
        ))
    return rows


def _proj(fn_name: str):
    """Late-bound project loader — keeps the git snapshot out of import time."""
    def load():
        from . import project
        return getattr(project, fn_name)()
    return load


def run() -> None:
    for name, fn in (("notes", notes), ("verdicts", verdicts), ("visits", visits),
                     ("film_review", film_reviews),
                     ("collection", lambda: __import__(
                         "exo.loaders.collection", fromlist=["load"]).load()),
                     ("recipe", lambda: recipes() + recipes_blog()), ("open_thread", open_threads),
                     ("post", posts),
                     ("draft", lambda: __import__(
                         "exo.loaders.drafts", fromlist=["load"]).load()),
                     ("item", items), ("item_event", item_events),
                     # the owner's repos, from the snapshot `wh scan-projects` writes
                     ("project", _proj("repos")), ("project_commit", _proj("commits")),
                     ("project_doc", _proj("docs")), ("project_open", _proj("open_work"))):
        rows = fn()
        for who, extra in plugins.t1_loaders().get(name, ()):
            add = extra()
            print(f"  t1/{name:10s} + {len(add):>5,} rows from {who}")
            rows = rows + add
        path = io.write_zone("t1", name, rows)
        print(f"  t1/{name:10s} {len(rows):>7,} rows -> {path.name}")

    # Zones no core loader writes at all — an instance's own T1 material.
    core = {"notes", "verdicts", "visits", "film_review", "collection", "recipe",
            "open_thread", "post", "draft", "item", "item_event",
            "project", "project_commit", "project_doc", "project_open"}
    for name, contributors in sorted(plugins.t1_loaders().items()):
        if name in core:
            continue
        rows = [r for _, fn in contributors for r in fn()]
        path = io.write_zone("t1", name, rows)
        print(f"  t1/{name:10s} {len(rows):>7,} rows -> {path.name}  (plugin)")


# ─────────────────────────────── blog posts ──────────────────────────
# Body cap. The longest post is a wrapped retrospective; the cap exists so one
# outlier can't dominate the D1 bundle, and 20k holds ~99% of them whole.
_POST_BODY_CAP = 20_000


def posts() -> list[Row]:
    """t1_post: the published blog — the mirrored blog posts, all of it.

    The dir was already synced for `recipes_blog`, which reads the 10 posts with
    `type: recipe` and ignores the other 309. That made the blog the one body of
    authored writing the read surface could not reach: notes are indexed, chat is
    indexed, and the finished, public, edited version of the same thinking was
    invisible.

    Every row carries its live URL (`permalink: /posts/:title/` over
    the live site, keyed on the frontmatter `slug`, which is what Jekyll
    actually resolves `:title` to here — not the filename). So an answer can
    always hand back a link instead of a paraphrase.

    Nothing here is a privacy decision: it is on the public internet already.
    """
    src = config.POSTS
    if not src.exists():
        print("  posts: no mirrored posts — run `wh sync-raw`")
        return []
    rows: list[Row] = []
    seen_slugs: dict[str, list[str]] = {}
    for path in io.markdown(src):
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        slug = _s(fm.get("slug")) or path.stem
        title = _s(fm.get("title"))
        if not title:
            continue
        body = body.strip()
        published = _s(fm.get("publish_datetime")) or None
        # id keys on the FILENAME, not the slug. Jekyll guarantees filenames are
        # unique; it does not guarantee slugs are, and this blog has a pair that
        # collide (the March 2024 LAAD edition carries February's slug). Keying
        # on the slug would give two different posts one id — one vector for
        # both, and a label lifted from whichever the join happened to pick.
        seen_slugs.setdefault(slug, []).append(path.name)
        rows.append(Row(
            tier="t1", zone="post", source="blog", author="human",
            created=published, origin_ref=slug,
            id=stable_id("posts", path.stem),
            payload={
                "slug": slug,
                "title": title,
                "description": _s(fm.get("description")),
                "kind": _s(fm.get("type")) or "post",
                "published": (published or "")[:10],
                "tags": json.dumps([_s(t) for t in (fm.get("tags") or [])
                                    if not str(t).isdigit()]),
                "url": config.post_url(slug),
                "words": len(body.split()),
                "body": body[:_POST_BODY_CAP],
            },
        ))
    # Loud, because the consequence is off-site and invisible from here: two
    # posts sharing a slug share a permalink, so one of them is unreachable on
    # the live blog and any link this store hands out for the pair is a coin
    # flip. Nothing downstream can repair that — the fix is in the frontmatter.
    for slug, files in sorted(seen_slugs.items()):
        if len(files) > 1:
            print(f"  posts: WARNING slug '{slug}' claimed by {len(files)} posts "
                  f"({', '.join(files)}) — they collide on the live site, and the "
                  "URL this store publishes for them is ambiguous")
    return rows
