"""`wh scan-projects` — snapshot his repos into raw/projects.json.

The workshop floor is the one part of his life the store could not see. Notes,
scrobbles, ratings and events were all ingested; the ~40 repos those notes are
*about* were not, so a reader could recite the thesis of a project and not know
whether it had been touched since March, or that it exists at all.

Why a snapshot rather than a loader that walks git directly: the ETL is split
(ADR-0005). The laptop holds the checkouts; the cloud leg rebuilds from `raw/`
and has none of them. A loader reading `.git` would therefore build 4 populated
zones on the laptop and 4 empty ones in CI — and the cloud publish would then
overwrite the served copy with nothing. So the git reading happens here, on the
machine that has the repos, and the result lands in `raw/` like every other
export. The loader downstream reads only the snapshot.

Four things are captured, because "aware of my projects" is four questions:

    repo        what exists, where it points, how alive it is
    commits     what he actually worked on, and when
    docs        README / CONTEXT / ADRs / plans — the arguments each repo makes
    open work   TODO markers, unchecked plan items, uncommitted files

No source code is captured, ever. Prose and metadata only.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path

from .. import config

# Recency bands. A repo is not "abandoned" because it is finished, so the names
# describe heat, not judgement — `dormant` includes everything that shipped.
_BANDS = ((21, "active"), (90, "warm"), (365, "stalled"))

_LANG = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".rb": "ruby", ".go": "go",
    ".rs": "rust", ".swift": "swift", ".java": "java", ".c": "c", ".h": "c",
    ".cpp": "c++", ".sh": "shell", ".zsh": "shell", ".sql": "sql", ".md": "markdown",
    ".html": "html", ".css": "css", ".scss": "css", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".toml": "toml", ".ipynb": "notebook", ".liquid": "liquid",
    ".tox": "touchdesigner", ".vue": "vue", ".lua": "lua", ".r": "r",
}

# Files whose TEXT we take. Everything else is only ever counted.
_DOC_EXT = {".md", ".markdown"}
_MARKER_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".rb", ".go", ".rs",
               ".swift", ".sh", ".zsh", ".sql", ".css", ".scss", ".html", ".vue"}

_MARKER = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]\s*(.+)")
_UNCHECKED = re.compile(r"^\s*[-*]\s+\[ \]\s+(.+)")

_DOC_BODY_CAP = 20_000     # a README is prose; an 80KB one is a generated dump
_COMMITS_CAP = 3_000       # per repo; the blog is the only thing near this
_TODO_CAP = 60             # per repo, per kind — a marker sweep is a smell, not a list
_DIRTY_CAP = 40
_FILE_SCAN_CAP = 4_000     # tracked files opened for markers


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    """Run git in `repo`, returning stdout ('' on any failure).

    Failure is normal here, not exceptional: a repo with no commits has no HEAD,
    a repo with no upstream has no @{u}. Each caller decides what the blank means.
    """
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _find_repos(root: Path) -> list[Path]:
    """Directories holding a .git, at most PROJECTS_MAX_DEPTH below root.

    A repo found at depth 1 stops the walk into it, so a vendored checkout or a
    submodule inside a project is not counted as a project of its own.
    """
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > config.PROJECTS_MAX_DEPTH:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir())
        except (PermissionError, OSError):
            return
        for child in children:
            if child.name.startswith(".") or child.name in config.PROJECTS_DENY:
                continue
            if (child / ".git").exists():
                found.append(child)
                continue          # do not descend into a repo
            walk(child, depth + 1)

    walk(root, 1)
    return found


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - when).days


def _status(days: int | None) -> str:
    if days is None:
        return "empty"
    for limit, name in _BANDS:
        if days <= limit:
            return name
    return "dormant"


def _github(remote: str) -> str:
    """owner/repo from either remote form, '' if not GitHub."""
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", remote or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _read(path: Path, cap: int = _DOC_BODY_CAP) -> str:
    try:
        if path.stat().st_size > cap * 8:
            return path.read_text(encoding="utf-8", errors="replace")[:cap]
        return path.read_text(encoding="utf-8", errors="replace")[:cap]
    except (OSError, ValueError):
        return ""


def _first_paragraph(body: str) -> str:
    """The repo's own one-line claim about itself: first real prose in its README.

    Skips the title, badges and blockquote taglines so the description is a
    sentence rather than '# project-name'.
    """
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block or block.startswith(("#", "!", "[!", "<!--", "```", "|")):
            continue
        block = re.sub(r"^>\s?", "", block, flags=re.M)
        block = " ".join(block.split())
        if len(block) > 20:
            return block[:600]
    return ""


def _docs_for(repo: Path, tracked: list[str]) -> list[dict]:
    """Prose worth keeping, in the order a person would read it."""
    want: list[tuple[str, str]] = []           # (relative path, kind)
    tracked_set = set(tracked)

    for name, kind in (("README.md", "readme"), ("CONTEXT.md", "context"),
                       ("CLAUDE.md", "claude"), ("ARCHITECTURE.md", "context"),
                       ("ROADMAP.md", "plan"), ("CUTOVER.md", "plan")):
        if name in tracked_set:
            want.append((name, kind))

    for rel in tracked:
        if Path(rel).suffix.lower() not in _DOC_EXT:
            continue
        if rel.startswith("docs/adr/"):
            want.append((rel, "adr"))
        elif rel.startswith(("docs/plans/", "plans/")):
            want.append((rel, "plan"))
        elif rel.startswith("docs/") and rel.count("/") == 1:
            want.append((rel, "doc"))

    out: list[dict] = []
    seen: set[str] = set()
    for rel, kind in want:
        if rel in seen:
            continue
        seen.add(rel)
        body = _read(repo / rel)
        if not body.strip():
            continue
        title = ""
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        out.append({
            "path": rel,
            "kind": kind,
            "title": title or Path(rel).stem,
            "body": body,
        })
    return out


def _open_work(repo: Path, tracked: list[str], docs: list[dict]) -> list[dict]:
    """What is visibly unfinished: markers in code, unchecked plan items, dirt."""
    out: list[dict] = []

    markers = 0
    for rel in tracked[:_FILE_SCAN_CAP]:
        if markers >= _TODO_CAP:
            break
        if Path(rel).suffix.lower() not in _MARKER_EXT:
            continue
        path = repo / rel
        try:
            if path.stat().st_size > 400_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "TODO" not in text and "FIXME" not in text and "HACK" not in text and "XXX" not in text:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            m = _MARKER.search(line)
            if not m:
                continue
            out.append({"kind": "marker", "path": rel, "line": n,
                        "text": f"{m.group(1)}: {' '.join(m.group(2).split())[:300]}"})
            markers += 1
            if markers >= _TODO_CAP:
                break

    boxes = 0
    for doc in docs:
        if doc["kind"] not in ("plan", "context", "readme"):
            continue
        for n, line in enumerate(doc["body"].splitlines(), 1):
            m = _UNCHECKED.match(line)
            if not m:
                continue
            out.append({"kind": "unchecked", "path": doc["path"], "line": n,
                        "text": " ".join(m.group(1).split())[:300]})
            boxes += 1
            if boxes >= _TODO_CAP:
                break
        if boxes >= _TODO_CAP:
            break

    for line in (_git(repo, "status", "--porcelain") or "").splitlines()[:_DIRTY_CAP]:
        code, _, rel = line.partition(" ")
        out.append({"kind": "uncommitted", "path": rel.strip() or line[3:],
                    "line": 0, "text": f"{code.strip() or '??'} uncommitted"})

    return out


def _scan_repo(repo: Path, root: Path) -> tuple[dict, list[dict], list[dict], list[dict]]:
    slug = str(repo.relative_to(root))
    group = slug.split("/")[0] if "/" in slug else ""

    tracked = [f for f in (_git(repo, "ls-files") or "").splitlines() if f][:20_000]

    langs: dict[str, int] = {}
    for rel in tracked:
        lang = _LANG.get(Path(rel).suffix.lower())
        if lang:
            langs[lang] = langs.get(lang, 0) + 1
    top = sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))[:4]

    log = _git(repo, "log", "--no-merges", f"-{_COMMITS_CAP}",
               "--pretty=format:%H\x1f%aI\x1f%an\x1f%s")
    commits: list[dict] = []
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, when, who, subject = parts
        commits.append({"repo": slug, "sha": sha, "date": when,
                        "committed_by": who, "subject": subject[:400]})

    last = commits[0]["date"] if commits else None
    first = commits[-1]["date"] if commits else None
    days = _days_since(last)

    docs = _docs_for(repo, tracked)
    readme = next((d for d in docs if d["kind"] == "readme"), None)
    context = next((d for d in docs if d["kind"] == "context"), None)
    description = _first_paragraph(readme["body"] if readme else "") \
        or _first_paragraph(context["body"] if context else "")

    porcelain = (_git(repo, "status", "--porcelain") or "").splitlines()
    ahead = _git(repo, "rev-list", "--count", "@{u}..HEAD")

    meta = {
        "slug": slug,
        "name": repo.name,
        "group": group,
        "path": str(repo),
        "remote": _git(repo, "remote", "get-url", "origin"),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit_count": int(_git(repo, "rev-list", "--count", "HEAD") or 0),
        "first_commit": first,
        "last_commit": last,
        "days_idle": days,
        "status": _status(days),
        "languages": ", ".join(f"{k} {v}" for k, v in top),
        "file_count": len(tracked),
        "doc_count": len(docs),
        "dirty": sum(1 for line in porcelain if not line.startswith("??")),
        "untracked": sum(1 for line in porcelain if line.startswith("??")),
        "unpushed": int(ahead) if ahead.isdigit() else 0,
        "description": description,
    }
    meta["github"] = _github(meta["remote"])

    open_work = [{"repo": slug, **w} for w in _open_work(repo, tracked, docs)]
    docs = [{"repo": slug, **d} for d in docs]
    return meta, commits, docs, open_work


def run() -> int:
    root = config.PROJECTS_ROOT
    if not root.exists():
        print(f"scan-projects: {root} does not exist — nothing to scan")
        return 1

    repos = _find_repos(root)
    print(f"scan-projects: {len(repos)} repos under {root}")

    all_meta, all_commits, all_docs, all_work = [], [], [], []
    for repo in repos:
        meta, commits, docs, work = _scan_repo(repo, root)
        all_meta.append(meta)
        all_commits.extend(commits)
        all_docs.extend(docs)
        all_work.extend(work)
        print(f"  {meta['slug']:<40s} {meta['status']:<8s} "
              f"{meta['commit_count']:>5,} commits  {len(docs):>3} docs  {len(work):>3} open")

    snapshot = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(root),
        "repos": all_meta,
        "commits": all_commits,
        "docs": all_docs,
        "open_work": all_work,
    }

    # Refuse to replace a good snapshot with an empty one. Same reasoning as the
    # event fetchers: a scan that finds nothing (wrong root, revoked disk access)
    # must not be the reason the read surface forgets he builds things.
    out = config.PROJECTS_SNAPSHOT
    if not all_meta and out.exists():
        print("scan-projects: found no repos — keeping the existing snapshot")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    size = out.stat().st_size / 1_000_000
    print(f"scan-projects: {len(all_meta)} repos · {len(all_commits):,} commits · "
          f"{len(all_docs)} docs · {len(all_work)} open items -> {out.name} ({size:.1f}MB)")
    return 0
