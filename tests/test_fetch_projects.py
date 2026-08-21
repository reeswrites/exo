"""The workshop over the API — same snapshot, different road.

`loaders/project.py` reads `raw/projects.json` and must never learn which road
produced it. That indirection is the reason the workshop works at all: a loader
reading `.git` directly would build four populated zones on the laptop and four
empty ones in CI, and the cloud publish would overwrite the served copy with
nothing. So the tests that matter are about the SHAPE matching, and about the
one capture that is deliberately gone.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from exo.scripts_impl import fetch_projects as fp
from exo.scripts_impl import scan_projects as scan


def _make_tarball(tmp_path: Path, files: dict[str, str], top="owner-repo-abc123") -> bytes:
    stage = tmp_path / "stage" / top
    stage.mkdir(parents=True)
    for rel, body in files.items():
        f = stage / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    archive = tmp_path / "t.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(stage, arcname=top)
    return archive.read_bytes()


# ───────────────────────── the snapshot shape ─────────────────────────


def test_the_snapshot_carries_exactly_the_keys_the_loader_reads():
    # The four lists loaders/project.py reads, plus the provenance the brief
    # uses. A missing key here is an empty zone three stages downstream.
    from exo.scripts_impl import fetch_projects
    src = Path(fetch_projects.__file__).read_text()
    for key in ("repos", "commits", "docs", "open_work", "generated_at", "root"):
        assert f'"{key}":' in src


def test_repo_meta_matches_the_fields_scan_projects_emits(tmp_path, monkeypatch):
    # Every key the local road writes must exist on this one, or a zone column
    # vanishes depending on which road last ran.
    local_keys = {
        "slug", "name", "group", "path", "remote", "branch", "commit_count",
        "first_commit", "last_commit", "days_idle", "status", "languages",
        "file_count", "doc_count", "dirty", "untracked", "unpushed",
        "description", "github",
    }
    monkeypatch.setattr(fp, "_commits", lambda *_a: ([], 0))
    monkeypatch.setattr(fp, "_languages", lambda *_a: "python 10")
    monkeypatch.setattr(fp, "_tarball", lambda *_a, **_k: None)
    meta, _, _, _ = fp._one(
        {"full_name": "o/r", "name": "r", "default_branch": "main",
         "clone_url": "https://github.com/o/r.git", "pushed_at": "2026-08-01T00:00:00Z",
         "description": "a thing"},
        set(), tmp_path)
    assert set(meta) == local_keys


def test_working_tree_counters_are_zero_not_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_commits", lambda *_a: ([], 0))
    monkeypatch.setattr(fp, "_languages", lambda *_a: "")
    monkeypatch.setattr(fp, "_tarball", lambda *_a, **_k: None)
    meta, _, _, _ = fp._one(
        {"full_name": "o/r", "name": "r", "pushed_at": "2026-08-01T00:00:00Z"},
        set(), tmp_path)
    assert meta["dirty"] == 0 and meta["untracked"] == 0 and meta["unpushed"] == 0


# ────────────────── the capture that is deliberately gone ──────────────────


def test_no_uncommitted_rows_come_off_a_tarball(tmp_path):
    # A dirty tree is a fact about a working session. A six-hour-old snapshot of
    # it does not report stale information, it reports wrong information — work
    # committed before lunch, presented as current. It leaves by itself: there
    # is no .git in a tarball, so _open_work's `git status` yields nothing.
    tree = tmp_path / "repo"
    (tree / "docs" / "plans").mkdir(parents=True)
    (tree / "app.py").write_text("# TODO: wire the thing\n", encoding="utf-8")
    (tree / "docs" / "plans" / "p.md").write_text(
        "# Plan\n\n- [ ] an unchecked item\n", encoding="utf-8")
    tracked = fp._tracked(tree)
    docs = scan._docs_for(tree, tracked)
    work = scan._open_work(tree, tracked, docs)
    kinds = {w["kind"] for w in work}
    assert "uncommitted" not in kinds
    assert kinds == {"marker", "unchecked"}, (
        "markers and unchecked items live in tracked files and must survive")


# ──────────────────────────── the tarball road ────────────────────────────


def test_a_tarball_is_extracted_and_its_files_are_tracked(tmp_path, monkeypatch):
    blob = _make_tarball(tmp_path, {"README.md": "# r\n\nA real description here.\n",
                                    "src/app.py": "x = 1\n"})
    monkeypatch.setattr(fp, "_get", lambda _p, raw=False: (blob, {}))
    root = fp._tarball("o/r", "main", tmp_path / "out")
    assert root is not None, "_tarball must create its own destination"
    assert set(fp._tracked(root)) == {"README.md", "src/app.py"}


def test_dot_directories_are_skipped_the_way_the_local_walk_skips_them(tmp_path):
    tree = tmp_path / "repo"
    (tree / ".github" / "workflows").mkdir(parents=True)
    (tree / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (tree / "README.md").write_text("# r\n", encoding="utf-8")
    assert fp._tracked(tree) == ["README.md"]


def test_a_member_escaping_the_destination_is_not_extracted(tmp_path, monkeypatch):
    # These are bytes from a network unpacked into a writable directory. The
    # classic tar trap has to be closed even when the source is trusted today.
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "ok.txt").write_text("fine", encoding="utf-8")
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(stage / "ok.txt", arcname="top/ok.txt")
        tf.add(stage / "ok.txt", arcname="top/../../escaped.txt")
    monkeypatch.setattr(fp, "_get", lambda _p, raw=False: (archive.read_bytes(), {}))
    dest = tmp_path / "out"
    fp._tarball("o/r", "main", dest)
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


# ─────────────────────────── commit counting ───────────────────────────


def test_the_commit_total_comes_from_the_link_header():
    # Counting commits is otherwise a full walk of the history. One request with
    # per_page=1 and the last-page number IS the count.
    headers = {"Link": '<https://api.github.com/repositories/1/commits?per_page=1&page=2>; '
                       'rel="next", <https://api.github.com/repositories/1/commits'
                       '?per_page=1&page=1793>; rel="last"'}
    assert fp._total_from_link(headers, 1) == 1793


def test_a_single_page_repo_falls_back_to_the_count_it_was_given():
    assert fp._total_from_link({}, 7) == 7


def test_an_empty_repository_is_not_an_error(monkeypatch):
    import urllib.error

    def boom(_path, raw=False):
        raise urllib.error.HTTPError("u", 409, "Conflict", {}, None)

    monkeypatch.setattr(fp, "_get", boom)
    assert fp._commits("o/r", "r") == ([], 0)


# ───────────────────────────── the floor ─────────────────────────────


def test_an_empty_fetch_never_replaces_a_good_snapshot(tmp_path, monkeypatch, capsys):
    snap = tmp_path / "projects.json"
    snap.write_text(json.dumps({"repos": [{"slug": "real"}]}), encoding="utf-8")
    monkeypatch.setattr(fp.config, "PROJECTS_SNAPSHOT", snap)
    monkeypatch.setattr(fp, "_repos", lambda _d: [])
    assert fp.run() == 1
    assert json.loads(snap.read_text())["repos"] == [{"slug": "real"}], (
        "a revoked token or a rate limit must not be why the surface forgets "
        "the workshop exists")


def test_a_repo_never_pushed_to_is_not_a_project(monkeypatch):
    monkeypatch.setattr(fp, "_paged", lambda _p: [
        {"name": "real", "pushed_at": "2026-08-01T00:00:00Z"},
        {"name": "reserved", "pushed_at": None},
    ])
    assert [r["name"] for r in fp._repos(set())] == ["real"]


def test_the_deny_list_still_applies_over_the_api(monkeypatch):
    monkeypatch.setattr(fp, "_paged", lambda _p: [
        {"name": "keep", "pushed_at": "2026-08-01T00:00:00Z"},
        {"name": "warehouse", "pushed_at": "2026-08-01T00:00:00Z"},
    ])
    assert [r["name"] for r in fp._repos({"warehouse"})] == ["keep"]
