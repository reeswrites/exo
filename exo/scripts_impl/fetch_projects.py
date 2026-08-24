"""`exo fetch-projects` — the workshop over the GitHub API instead of the disk.

`scan-projects` states its own constraint plainly: the laptop holds the
checkouts, so a loader reading `.git` "would build 4 populated zones on the
laptop and 4 empty ones in CI — and the cloud publish would then overwrite the
served copy with nothing." The snapshot indirection buys exactly that safety.
This inverts it — GitHub has every repo with a remote, so the cloud can read
them all and the workshop stops going stale whenever the lid is shut (ADR-0015
§6).

**It writes the same snapshot.** `loaders/project.py` reads `raw/projects.json`
and must never learn which road produced it; that is the whole point of there
being a snapshot at all. Everything below exists to fill the same shape.

## One request per repo, not one per file

The obvious approach — walk the tree, fetch each blob — costs thousands of
requests for a workshop this size and pays it again whenever a file changes.
The tarball endpoint hands back a whole ref in ONE request, after which the
scanning is a local filesystem problem that `scan_projects` has already solved.
So its helpers are imported rather than reimplemented: same doc selection, same
marker rules, same language counting, same README-first-paragraph description.
Two roads into the record must not disagree about what a doc is.

## What is lost, and why that is the right trade

`uncommitted` open-work items are gone, and they leave by themselves: a tarball
has no `.git`, so `_open_work`'s `git status` call returns nothing and emits no
rows. That is deliberate rather than incidental — it is the only capture that
would require a process running on the laptop, and it is the fastest-decaying
signal in the record. A dirty tree is a fact about a working session; a snapshot
of it six hours old does not report stale information, it reports *wrong*
information — work finished and committed before lunch, presented as current.

`dirty`, `untracked` and `unpushed` are reported as 0 for the same reason. They
are working-tree facts and this road cannot see a working tree. Zero is honest
here: there is no uncommitted work *on GitHub*.

Repos with no remote, and repos never pushed, are simply not in the workshop —
the same trade `EXO_PROJECTS_DENY` already makes, stated out loud.

## The token is a better boundary than the deny list

`PROJECTS_DENY` is scan-time exclusion: a repo stays out because this code chose
not to read it. A fine-grained token scoped to selected repositories means
GitHub refuses on our behalf, so a bug in the deny list cannot expose a repo
that was never granted. The deny list still applies — two independent refusals
is the shape the rest of the record uses.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config
from . import scan_projects as scan

API = "https://api.github.com"
UA = "exo/1.0"
PER_PAGE = 100
MAX_COMMIT_PAGES = 30      # 3,000 commits, matching scan_projects' _COMMITS_CAP


def _token() -> str:
    tok = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not tok:
        raise SystemExit(
            "fetch-projects: set GITHUB_TOKEN. A fine-grained token scoped to the "
            "repositories you want in the workshop is the right one — it makes "
            "GitHub enforce the exclusion rather than this code.")
    return tok


def _get(path: str, *, raw: bool = False):
    """One API call. Returns (body, headers); body is bytes when raw."""
    url = path if path.startswith("http") else f"{API}{path}"
    req = urllib.request.Request(url, headers={
        "authorization": f"Bearer {_token()}",
        "accept": "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": UA,
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
        return (body if raw else json.loads(body or b"null")), dict(resp.headers)


def _paged(path: str, cap: int = 50) -> list[dict]:
    out: list[dict] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, cap + 1):
        batch, _ = _get(f"{path}{sep}per_page={PER_PAGE}&page={page}")
        if not batch:
            break
        out.extend(batch)
        if len(batch) < PER_PAGE:
            break
    return out


def _total_from_link(headers: dict, fallback: int) -> int:
    """Total items from a `Link: <...page=N>; rel="last"` header.

    The count of commits in a repo is otherwise a full walk of its history. One
    request with per_page=1 and this header is the whole answer — GitHub puts
    the last page number in it, and with one item per page that number IS the
    count.
    """
    link = headers.get("Link") or headers.get("link") or ""
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', link)
    return int(m.group(1)) if m else fallback


def _repos(deny: set[str]) -> list[dict]:
    """The owner's repos, minus the deny list and anything never pushed."""
    got = _paged("/user/repos?affiliation=owner&sort=pushed")
    out = []
    for r in got:
        name = r.get("name") or ""
        if not name or name in deny:
            continue
        # A repo that has never been pushed to has no commits, no tree and no
        # tarball. It is not a project yet; it is a name someone reserved.
        if not r.get("pushed_at"):
            continue
        out.append(r)
    return out


def _commits(full_name: str, slug: str) -> tuple[list[dict], int]:
    """Commit subjects and dates, newest first, plus the true total.

    Merges are excluded to match `scan_projects`, which passes --no-merges: a
    merge commit is a fact about a branch, not about work done.
    """
    try:
        _, headers = _get(f"/repos/{full_name}/commits?per_page=1")
    except urllib.error.HTTPError as exc:
        if exc.code == 409:      # empty repository
            return [], 0
        raise
    total = _total_from_link(headers, 1)

    out: list[dict] = []
    for raw in _paged(f"/repos/{full_name}/commits", cap=MAX_COMMIT_PAGES):
        if len(raw.get("parents") or []) > 1:
            continue
        commit = raw.get("commit") or {}
        author = commit.get("author") or {}
        subject = (commit.get("message") or "").splitlines()[0] if commit.get("message") else ""
        out.append({
            "repo": slug,
            "sha": raw.get("sha", ""),
            "date": author.get("date") or "",
            "committed_by": author.get("name") or "",
            "subject": subject[:400],
        })
    return out, total


def _languages(full_name: str) -> str:
    """`"python 42, markdown 12"`, matching scan_projects' shape.

    The NUMBERS differ: GitHub counts bytes, the local scan counts files. Both
    answer "what is this written in" and neither is compared across roads, so
    the shape is what has to match rather than the values.
    """
    try:
        langs, _ = _get(f"/repos/{full_name}/languages")
    except urllib.error.HTTPError:
        return ""
    top = sorted((langs or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    return ", ".join(f"{k.lower()} {v}" for k, v in top)


def _tarball(full_name: str, ref: str, dest: Path) -> Path | None:
    """One ref, one request, extracted. Returns the extracted root."""
    try:
        blob, _ = _get(f"/repos/{full_name}/tarball/{ref}", raw=True)
    except urllib.error.HTTPError as exc:
        print(f"    tarball failed ({exc.code}) — metadata only")
        return None
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "repo.tar.gz"
    archive.write_bytes(blob)
    with tarfile.open(archive) as tf:
        # GitHub wraps everything in one top-level directory whose name carries
        # the sha. Members are checked rather than trusted: a path escaping the
        # destination is a classic tar trap, and this unpacks bytes from a
        # network into a directory the process can write to.
        root = None
        safe = []
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue
            if member.issym() or member.islnk():
                continue
            safe.append(member)
            top = member.name.split("/")[0]
            root = root or top
        # `filter="data"` is the maintained version of the check above —
        # it rejects absolute paths, parent traversal, links and devices, and
        # drops ownership metadata. Both are kept: the pre-filter SKIPS a bad
        # member so one oddity does not cost the whole repo, and this raises on
        # anything it still does not like. It also becomes the default in 3.14,
        # so naming it now is what stops the behaviour changing underneath us.
        tf.extractall(dest, members=safe, filter="data")
    archive.unlink(missing_ok=True)
    return (dest / root) if root else None


def _tracked(root: Path) -> list[str]:
    """Every file in the extracted tree, as repo-relative paths.

    Stands in for `git ls-files`, and the difference is in our favour: a tarball
    contains exactly the tracked files, because untracked ones were never
    committed. Dot-directories are skipped the way the local walk skips them.
    """
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(str(rel))
        if len(out) >= 20_000:
            break
    return out


def _one(repo: dict, deny: set[str], workdir: Path) -> tuple[dict, list, list, list]:
    full_name = repo["full_name"]
    slug = repo["name"]
    commits, total = _commits(full_name, slug)

    last = commits[0]["date"] if commits else (repo.get("pushed_at") or None)
    first = commits[-1]["date"] if commits else None
    days = scan._days_since(last)

    docs: list[dict] = []
    open_work: list[dict] = []
    tracked: list[str] = []
    tree = _tarball(full_name, repo.get("default_branch") or "HEAD", workdir)
    if tree:
        tracked = _tracked(tree)
        docs = scan._docs_for(tree, tracked)
        # `_open_work` shells out to `git status` for its `uncommitted` rows.
        # There is no .git in a tarball, so that call returns nothing and those
        # rows do not exist — which is the decision, arriving for free.
        open_work = scan._open_work(tree, tracked, docs)

    readme = next((d for d in docs if d["kind"] == "readme"), None)
    context = next((d for d in docs if d["kind"] == "context"), None)
    description = (
        scan._first_paragraph(readme["body"] if readme else "")
        or scan._first_paragraph(context["body"] if context else "")
        or (repo.get("description") or "")[:600]
    )

    meta = {
        "slug": slug,
        "name": slug,
        # No directory structure to group by on this road. Kept as a key with an
        # empty value rather than dropped: the loader reads it, and an absent
        # key and an empty one are different failures to debug.
        "group": "",
        "path": "",
        "remote": repo.get("clone_url") or "",
        "branch": repo.get("default_branch") or "",
        "commit_count": total,
        "first_commit": first,
        "last_commit": last,
        "days_idle": days,
        "status": scan._status(days),
        "languages": _languages(full_name),
        "file_count": len(tracked),
        "doc_count": len(docs),
        # Working-tree facts, and this road has no working tree. Zero is the
        # honest answer: there is no uncommitted work on GitHub.
        "dirty": 0,
        "untracked": 0,
        "unpushed": 0,
        "description": description,
        "github": full_name,
    }
    return (meta,
            commits,
            [{"repo": slug, **d} for d in docs],
            [{"repo": slug, **w} for w in open_work])


def run() -> int:
    deny = set(config.PROJECTS_DENY)
    try:
        repos = _repos(deny)
    except urllib.error.HTTPError as exc:
        print(f"fetch-projects: listing repos failed ({exc.code} {exc.reason})")
        return 1
    print(f"fetch-projects: {len(repos)} repo(s) after the deny list")

    all_meta, all_commits, all_docs, all_work = [], [], [], []
    for repo in repos:
        with tempfile.TemporaryDirectory(prefix="exo-proj-") as tmp:
            try:
                meta, commits, docs, work = _one(repo, deny, Path(tmp))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, tarfile.TarError) as exc:
                # One rotten repo must not cost the whole workshop. The snapshot
                # floor below still protects against losing all of them.
                print(f"  {repo.get('name', '?'):<40s} FAILED ({exc}) — skipped")
                continue
        all_meta.append(meta)
        all_commits.extend(commits)
        all_docs.extend(docs)
        all_work.extend(work)
        print(f"  {meta['slug']:<40s} {meta['status']:<8s} "
              f"{meta['commit_count']:>5,} commits  {len(docs):>3} docs  {len(work):>3} open")

    snapshot = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": "github",
        "repos": all_meta,
        "commits": all_commits,
        "docs": all_docs,
        "open_work": all_work,
    }

    # The same floor scan-projects keeps, for the same reason: a fetch that
    # finds nothing (a revoked token, a rate limit, an outage) must not be why
    # the read surface forgets the workshop exists.
    out = config.PROJECTS_SNAPSHOT
    if not all_meta and out.exists():
        print("fetch-projects: found no repos — keeping the existing snapshot")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    print(f"fetch-projects: {len(all_meta)} repos · {len(all_commits):,} commits · "
          f"{len(all_docs)} docs · {len(all_work)} open items -> {out.name} "
          f"({out.stat().st_size / 1_000_000:.1f}MB)")
    return 0
