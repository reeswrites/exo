"""The workshop (ADR-0011): scanning his repos, and the zones built from it.

Every test builds a throwaway git repo under tmp_path — nothing here reads the
real ~/Documents. The two properties worth guarding are that source code is never
captured (only prose and markers), and that a scan finding nothing cannot be the
reason the surface forgets he builds things.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from exo import config
from exo.loaders import project
from exo.scripts_impl import scan_projects


def _repo(root, name, files, commits=("first commit",)):
    """A real git repo — the scanner shells out to git, so a fake would prove nothing."""
    d = root / name
    d.mkdir(parents=True)
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
           "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(root)}
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True, env=env)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, env=env)
    for msg in commits:
        subprocess.run(["git", "-C", str(d), "commit", "-q", "--allow-empty", "-m", msg],
                       check=True, env=env)
    return d


@pytest.fixture
def workshop(tmp_path, monkeypatch):
    """A root with one live repo, one shelved under a group, one denied."""
    root = tmp_path / "Documents"
    root.mkdir()
    _repo(root, "alchemy", {
        "README.md": "# alchemy\n\n[![badge](x)](y)\n\nThe creation lab bench.\n",
        "docs/adr/0001-a-choice.md": "# ADR-0001 — A choice\n\nWe chose the other thing.\n",
        "run.py": "SECRET = 'do-not-publish'\n# TODO: collide unlinked atoms\n",
    }, commits=("first commit", "second commit"))
    _repo(root / "hiatus", "mosaic", {"README.md": "# mosaic\n\nA photo grid.\n"})
    _repo(root, "node_modules", {"README.md": "# vendored\n"})

    monkeypatch.setattr(config, "PROJECTS_ROOT", root)
    monkeypatch.setattr(config, "PROJECTS_SNAPSHOT", tmp_path / "projects.json")
    monkeypatch.setattr(config, "PROJECTS_DENY", ("node_modules",))
    monkeypatch.setattr(config, "PROJECTS_MAX_DEPTH", 2)
    project._CACHE.clear()
    return tmp_path / "projects.json"


def _snapshot(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scan_finds_repos_two_levels_down_and_honours_the_denylist(workshop):
    assert scan_projects.run() == 0
    slugs = {r["slug"] for r in _snapshot(workshop)["repos"]}
    assert slugs == {"alchemy", "hiatus/mosaic"}          # node_modules excluded


def test_a_shelved_repo_keeps_the_folder_it_was_filed_under(workshop):
    scan_projects.run()
    repos = {r["slug"]: r for r in _snapshot(workshop)["repos"]}
    assert repos["hiatus/mosaic"]["group"] == "hiatus"
    assert repos["alchemy"]["group"] == ""               # directly under the root


def test_description_is_prose_not_the_heading_or_a_badge(workshop):
    scan_projects.run()
    repos = {r["slug"]: r for r in _snapshot(workshop)["repos"]}
    assert repos["alchemy"]["description"] == "The creation lab bench."


def test_source_code_is_never_captured_only_its_markers(workshop):
    scan_projects.run()
    blob = workshop.read_text(encoding="utf-8")
    assert "do-not-publish" not in blob                   # the line itself never leaves
    todos = [w["text"] for w in _snapshot(workshop)["open_work"] if w["kind"] == "marker"]
    assert any("collide unlinked atoms" in t for t in todos)


def test_docs_are_captured_with_their_kind(workshop):
    scan_projects.run()
    docs = {(d["repo"], d["kind"]) for d in _snapshot(workshop)["docs"]}
    assert ("alchemy", "readme") in docs
    assert ("alchemy", "adr") in docs
    assert any("We chose the other thing" in d["body"] for d in _snapshot(workshop)["docs"])


def test_an_empty_scan_never_replaces_a_good_snapshot(workshop, monkeypatch):
    assert scan_projects.run() == 0
    before = workshop.read_text(encoding="utf-8")
    # the failure this guards: a wrong root, or revoked disk access, mid-sync
    monkeypatch.setattr(config, "PROJECTS_ROOT", workshop.parent / "nowhere-with-repos")
    (workshop.parent / "nowhere-with-repos").mkdir()
    assert scan_projects.run() == 1
    assert workshop.read_text(encoding="utf-8") == before


def test_zones_carry_the_t1_envelope(workshop):
    scan_projects.run()
    project._CACHE.clear()
    for rows in (project.repos(), project.commits(), project.docs(), project.open_work()):
        assert rows, "every zone should have rows for this fixture"
        for r in rows:
            assert (r.tier, r.author, r.grounds, r.regenerable) == ("t1", "human", True, False)


def test_commit_rows_are_one_per_commit_and_dated(workshop):
    scan_projects.run()
    project._CACHE.clear()
    rows = [r for r in project.commits() if r.payload["repo"] == "alchemy"]
    assert {r.payload["subject"] for r in rows} == {"first commit", "second commit"}
    assert all(r.created and r.created[:2] == "20" for r in rows)


def test_missing_snapshot_yields_empty_zones_rather_than_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_SNAPSHOT", tmp_path / "absent.json")
    project._CACHE.clear()
    assert project.repos() == [] and project.commits() == []
    assert project.docs() == [] and project.open_work() == []


def test_ids_are_stable_across_two_scans_of_an_unchanged_tree(workshop):
    scan_projects.run()
    project._CACHE.clear()
    first = {r.id for r in project.repos()} | {r.id for r in project.commits()}
    scan_projects.run()
    project._CACHE.clear()
    second = {r.id for r in project.repos()} | {r.id for r in project.commits()}
    assert first == second


def test_a_repo_keeps_its_id_when_its_state_moves(workshop):
    """The id is the noun, not its state — else the ledger mints 44 new projects a night."""
    scan_projects.run()
    project._CACHE.clear()
    before = {r.payload["slug"]: r.id for r in project.repos()}

    snap = _snapshot(workshop)
    for r in snap["repos"]:                     # a day passes: it goes cold and dirty
        r["days_idle"] = (r["days_idle"] or 0) + 40
        r["dirty"] = 3
        r["status"] = "warm"
    workshop.write_text(json.dumps(snap), encoding="utf-8")
    project._CACHE.clear()

    after = {r.payload["slug"]: r.id for r in project.repos()}
    assert before == after
