"""Garden-surface projections → cache zones, for Life Terminal's launchpad reads.

ON-DECK.md and maps/ are second-brain DISPLAY material — machine-maintained or
hand-curated views, none of them grounding, all disposable/regenerable (ADR-0028 says a
map may be cited by nothing). So they land in the CACHE namespace (grounds=false,
full-profile-only → wall-invisible), never a grounding tier. This is a rebuildable
projection while second-brain owns the files; `wh gardenindex` refreshes it.

  cache_ondeck           ON-DECK.md           → title, group, ord
  cache_maps             maps/*.md (+ .svg)   → kind, title, created, md_path, svg_path
  cache_links            slug → path          → resolves [[wikilink]] targets

open-questions is NO LONGER projected here: `cache_open_questions` is now a DuckDB view
over `t1_open_thread` (canonical), registered in catalog.py (B1 / 0004-vault-relocation
§⚠️). `links()` still reads it; it's just served by the view now.

Life Terminal reads these through `wh queryjson`; nothing here ever grounds.
"""
from __future__ import annotations

import re

import yaml

from .. import cache, config
from ..api import read


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        fm = yaml.safe_load(text[3:end]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, text[end + 4:].lstrip("\n")


def ondeck() -> list[dict]:
    base = config.VAULT
    f = base / "ON-DECK.md"
    if not f.exists():
        return []
    _fm, body = _split_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
    rows, group, ordn = [], None, 0
    for line in body.split("\n"):
        h = re.match(r"^##\s+(.+)", line)
        if h:
            group = h.group(1).strip()
            continue
        it = re.match(r"^-\s+(.+\S)\s*$", line)
        if it and group:
            title = it.group(1).strip().split(" — ")[0].strip()
            rows.append(cache.row("ondeck", {"title": title, "grp": group, "ord": ordn},
                                  source="second-brain.on-deck"))
            ordn += 1
    return rows


def maps() -> list[dict]:
    base = config.VAULT
    rows = []
    for p in sorted((base / "maps").glob("*.md")):
        fm, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        if fm.get("type") != "map":
            continue
        name = p.name
        kind = ("music" if "week-in-music" in name else
                "film" if "week-in-film" in name else
                "themes" if "theme-map" in name else "other")
        heading = next((l[2:].strip() for l in body.split("\n") if l.startswith("# ")), p.stem)
        svg = p.with_suffix(".svg")
        rows.append(cache.row("maps", {
            "kind": kind, "title": heading, "md_path": str(p.relative_to(base)),
            "svg_path": str(svg.relative_to(base)) if svg.exists() else "",
            "mtime": int(p.stat().st_mtime * 1000),
        }, source="second-brain.maps", created=str(fm.get("created", "") or "") or None))
    return rows


def captures() -> list[dict]:
    """cache_captures: newest-first reversible captures, for recentCaptures. Reads the
    Exo's own captures/ (the new home) AND any legacy life-terminal captures still in
    second-brain chat-logs, so display history is continuous across the retirement."""
    rows = []
    for p in sorted(config.CAPTURES.glob("*.md")):
        fm, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        rows.append(cache.row("captures", {
            "title": str(fm.get("title", p.stem)), "cap_source": str(fm.get("source", "warehouse")),
            "path": "captures/" + p.name, "body": body.strip()[:400],
        }, source="warehouse.captures", created=str(fm.get("date", "") or "")[:10] or None))
    legacy = config.VAULT / "chat-logs"
    for p in sorted(legacy.glob("*.md")) if legacy.exists() else []:
        if p.name == "promote-queue.md":
            continue
        fm, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        if str(fm.get("source", "")) != "life-terminal":
            continue
        rows.append(cache.row("captures", {
            "title": str(fm.get("title", p.stem)), "cap_source": "life-terminal",
            "path": "data/chat-logs/" + p.name, "body": body.strip()[:400],
        }, source="second-brain.chat-logs", created=str(fm.get("date", "") or "")[:10] or None))
    return rows


def links() -> list[dict]:
    """slug → vault-relative path, for resolveLink([[slug]]). Built from the note record
    (t1_notes) plus the display zones just written, so a wikilink target resolves."""
    seen: dict[str, str] = {}
    for (ref,) in read("SELECT origin_ref FROM t1_notes WHERE origin_ref <> ''"):
        seen.setdefault(_slug(ref), "data/" + ref)
    for (ref,) in read("SELECT path FROM cache_open_questions WHERE path <> ''"):
        seen.setdefault(_slug(ref), "data/" + ref)
    for (ref,) in read("SELECT md_path FROM cache_maps WHERE md_path <> ''"):
        seen.setdefault(_slug(ref), "data/" + ref)
    return [cache.row("links", {"slug": s, "path": pth}, source="warehouse.links", id=s)
            for s, pth in seen.items()]


def _slug(ref: str) -> str:
    return ref.rsplit("/", 1)[-1].removesuffix(".md")


def run() -> None:
    from .. import catalog
    # open-questions is NOT projected here anymore: cache_open_questions is a view over
    # t1_open_thread, registered by catalog.build() (B1 / 0004-vault-relocation §⚠️).
    od = ondeck(); cache.write("ondeck", od)
    mp = maps(); cache.write("maps", mp)
    cp = captures(); cache.write("captures", cp)
    catalog.build()          # register the zones above (+ cache_open_questions view) so links() can read them
    lk = links(); cache.write("links", lk)
    catalog.build()          # register cache_links
    print(f"  cache/ondeck {len(od):>4}   cache/maps {len(mp):>4}   "
          f"cache/captures {len(cp):>5,}   cache/links {len(lk):>6,}  (garden surface, grounds=false)")
