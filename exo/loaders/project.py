"""T1 projects — his repos, from the snapshot `wh scan-projects` writes.

Four zones, because the reader asks four different questions:

    project        what exists and how alive it is
    project_commit what he worked on, dated
    project_doc    README / CONTEXT / ADR / plan prose
    project_open   markers, unchecked plan items, uncommitted files

T1 and author=human throughout. A commit subject and an ADR are his words about
his own work — the same category as a note, not something the outside world
recorded about him. `regenerable` stays False for the same reason the vault's
projection does: losing these parquets costs a rescan of a live record, but the
record is not derived from a lower tier.

Absence is normal, not an error. CI rebuilds from `raw/`, and if the snapshot has
never been pushed the zones come out empty rather than failing the rebuild — the
publish shrink guard is what catches an unexpected empty, and that is its job.
"""
from __future__ import annotations

import json

from .. import config
from ..provenance import Row, stable_id

_SOURCE = "git"


def _id(zone: str, *key) -> str:
    """Identity from the NOUN, not from its state.

    The default Row id hashes every payload value, which is right for a scrobble
    and wrong for a repo: `days_idle`, `dirty` and `last_commit` all move, so a
    payload-derived id would mint a new row every night and the first_seen ledger
    would record 44 brand-new projects a day, forever.
    """
    return stable_id(_SOURCE, zone, *key)


_CACHE: dict[tuple[str, float], dict] = {}


def _snapshot() -> dict:
    """Parse once per run. Four zones read the same file, and it is megabytes."""
    path = config.PROJECTS_SNAPSHOT
    if path.exists():
        key = (str(path), path.stat().st_mtime)
        if key in _CACHE:
            return _CACHE[key]
    if not path.exists():
        print(f"  projects: no snapshot at {path} — run `wh scan-projects` (zones stay empty)")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _CACHE.clear()
        _CACHE[key] = data
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"  projects: unreadable snapshot ({e}) — zones stay empty")
        return {}


def repos() -> list[Row]:
    rows: list[Row] = []
    for r in _snapshot().get("repos", []):
        rows.append(Row(
            tier="t1", zone="project", source=_SOURCE, author="human",
            created=r.get("first_commit"), origin_ref=r["slug"],
            id=_id("project", r["slug"]),
            payload={
                "slug": r["slug"],
                "name": r.get("name", ""),
                # '' for a repo directly under the root, else the folder it was
                # filed under (hiatus/, one-offs/) — his own shelving, and the
                # only signal in the tree that a thing was set down deliberately.
                "grouping": r.get("group", ""),
                "github": r.get("github", ""),
                "remote": r.get("remote", ""),
                "branch": r.get("branch", ""),
                "status": r.get("status", ""),
                "description": r.get("description", ""),
                "languages": r.get("languages", ""),
                "commit_count": int(r.get("commit_count") or 0),
                "file_count": int(r.get("file_count") or 0),
                "doc_count": int(r.get("doc_count") or 0),
                "first_commit": r.get("first_commit") or "",
                "last_commit": r.get("last_commit") or "",
                "days_idle": int(r["days_idle"]) if r.get("days_idle") is not None else -1,
                "dirty": int(r.get("dirty") or 0),
                "untracked": int(r.get("untracked") or 0),
                "unpushed": int(r.get("unpushed") or 0),
                "local_path": r.get("path", ""),
            },
        ))
    return rows


def commits() -> list[Row]:
    rows: list[Row] = []
    for c in _snapshot().get("commits", []):
        rows.append(Row(
            tier="t1", zone="project_commit", source=_SOURCE, author="human",
            created=c.get("date"), origin_ref=f"{c['repo']}@{c['sha'][:10]}",
            id=_id("project_commit", c["repo"], c["sha"]),
            payload={
                "repo": c["repo"],
                "sha": c["sha"][:10],
                "subject": c.get("subject", ""),
                # Who git recorded. Almost always him; kept because a repo with a
                # second name in it is a collaboration, which is a fact about the
                # project a reader should not have to guess at.
                "committed_by": c.get("committed_by", ""),
                "committed_at": c.get("date", ""),
            },
        ))
    return rows


def docs() -> list[Row]:
    rows: list[Row] = []
    for d in _snapshot().get("docs", []):
        rows.append(Row(
            tier="t1", zone="project_doc", source=_SOURCE, author="human",
            origin_ref=f"{d['repo']}/{d['path']}",
            id=_id("project_doc", d["repo"], d.get("path", "")),
            payload={
                "repo": d["repo"],
                "path": d.get("path", ""),
                "kind": d.get("kind", ""),
                "title": d.get("title", ""),
                "body": d.get("body", ""),
                "chars": len(d.get("body", "")),
            },
        ))
    return rows


def open_work() -> list[Row]:
    rows: list[Row] = []
    for w in _snapshot().get("open_work", []):
        rows.append(Row(
            tier="t1", zone="project_open", source=_SOURCE, author="human",
            origin_ref=f"{w['repo']}/{w.get('path', '')}:{w.get('line', 0)}",
            id=_id("project_open", w["repo"], w.get("path", ""), w.get("line", 0),
                   w.get("text", "")),
            payload={
                "repo": w["repo"],
                "kind": w.get("kind", ""),      # marker | unchecked | uncommitted
                "path": w.get("path", ""),
                "line": int(w.get("line") or 0),
                "text": w.get("text", ""),
            },
        ))
    return rows
