"""The ledger — the one state a rebuild cannot regenerate, and how it travels.

`first_seen` answers "when did this store first see this row", which for the
many sources carrying no date of their own is the only temporal handle there is.
It is not derivable from the inputs, so a runner that starts without it does not
fail — it silently re-mints a life's worth of consumption with today's date, and
every step reports success. That has already happened once to the file next door
(`surface-log.json`) and is happening now to this one.

The tests that matter are about merge, not transport: the merge rule is what
makes it safe for two legs to both write, and safe to re-run.
"""
from __future__ import annotations

import json

import pytest

from exo.scripts_impl import ledger


@pytest.fixture(autouse=True)
def ledger_dir(tmp_path, monkeypatch):
    d = tmp_path / "_ledger"
    d.mkdir()
    monkeypatch.setattr(ledger.config, "LEDGER", d)
    return d


def _theirs(tmp_path, first_seen=None, log=None):
    src = tmp_path / "incoming"
    src.mkdir(exist_ok=True)
    if first_seen is not None:
        ledger._write_first_seen(src / ledger.FIRST_SEEN, first_seen)
    if log is not None:
        (src / ledger.SURFACE_LOG).write_text(json.dumps(log), encoding="utf-8")
    return src


# ───────────────────────────── the merge rule ─────────────────────────────


def test_the_earliest_sighting_wins():
    merged, _, back = ledger.earliest({"a": "2026-08-20"}, {"a": "2026-01-05"})
    assert merged["a"] == "2026-01-05"
    assert back == 1


def test_a_later_sighting_never_moves_a_stamp_forward():
    # The whole failure mode: a lane that minted and failed to push loses its
    # mints, and the next run re-mints them at a later date. A minimum cannot
    # move forward, so the loss costs nothing.
    merged, _, back = ledger.earliest({"a": "2026-01-05"}, {"a": "2026-08-20"})
    assert merged["a"] == "2026-01-05"
    assert back == 0


def test_merging_is_commutative():
    a, b = {"x": "2026-01-01", "y": "2026-05-05"}, {"y": "2026-02-02", "z": "2026-03-03"}
    assert ledger.earliest(a, b)[0] == ledger.earliest(b, a)[0]


def test_merging_is_idempotent():
    a = {"x": "2026-01-01"}
    once = ledger.earliest(a, {"x": "2026-02-02", "y": "2026-03-03"})[0]
    assert ledger.earliest(once, once)[0] == once


# ──────────────────────────── merge on disk ────────────────────────────


def test_first_seen_merges_rather_than_replaces(tmp_path, ledger_dir):
    ledger._write_first_seen(ledger_dir / ledger.FIRST_SEEN, {"local": "2026-08-01"})
    ledger.merge(str(_theirs(tmp_path, first_seen={"remote": "2026-02-02"})))
    got = ledger._read_first_seen(ledger_dir / ledger.FIRST_SEEN)
    assert got == {"local": "2026-08-01", "remote": "2026-02-02"}, (
        "a replace would drop whatever this run had already minted")


def test_the_surface_log_merges_the_same_way(tmp_path, ledger_dir):
    (ledger_dir / ledger.SURFACE_LOG).write_text(
        json.dumps({"_baseline": "2026-08-01", "t0_music": "2026-08-01"}), encoding="utf-8")
    ledger.merge(str(_theirs(tmp_path, log={"_baseline": "2026-01-01",
                                            "t1_notes": "2026-04-04"})))
    got = json.loads((ledger_dir / ledger.SURFACE_LOG).read_text())
    assert got["_baseline"] == "2026-01-01", "the baseline is a first sighting too"
    assert got["t1_notes"] == "2026-04-04"
    assert got["t0_music"] == "2026-08-01"


def test_an_empty_local_ledger_takes_everything(tmp_path, ledger_dir):
    ledger.merge(str(_theirs(tmp_path, first_seen={"a": "2026-01-01", "b": "2026-02-02"})))
    assert len(ledger._read_first_seen(ledger_dir / ledger.FIRST_SEEN)) == 2


# ────────────────────── the directory is the unit ──────────────────────


def test_an_unknown_ledger_file_is_copied_when_absent(tmp_path, ledger_dir):
    src = _theirs(tmp_path, first_seen={"a": "2026-01-01"})
    (src / "something-new.json").write_text('{"k": 1}', encoding="utf-8")
    ledger.merge(str(src))
    assert (ledger_dir / "something-new.json").exists(), (
        "the unit is the directory, so a ledger file added later must travel "
        "without anyone remembering to name it")


def test_an_unknown_ledger_file_is_not_merged_by_guessing(tmp_path, ledger_dir, capsys):
    (ledger_dir / "something-new.json").write_text('{"k": "mine"}', encoding="utf-8")
    src = _theirs(tmp_path, first_seen={"a": "2026-01-01"})
    (src / "something-new.json").write_text('{"k": "theirs"}', encoding="utf-8")
    ledger.merge(str(src))
    assert json.loads((ledger_dir / "something-new.json").read_text())["k"] == "mine"
    assert "no merge rule" in capsys.readouterr().out, (
        "silently picking a side is how a ledger stops being the earliest answer")


def test_export_takes_the_whole_directory(tmp_path, ledger_dir):
    ledger._write_first_seen(ledger_dir / ledger.FIRST_SEEN, {"a": "2026-01-01"})
    (ledger_dir / ledger.SURFACE_LOG).write_text("{}", encoding="utf-8")
    (ledger_dir / "later-addition.json").write_text("{}", encoding="utf-8")
    dest = tmp_path / "out"
    assert ledger.export(str(dest)) == 0
    assert {p.name for p in dest.iterdir()} == {
        ledger.FIRST_SEEN, ledger.SURFACE_LOG, "later-addition.json"}


def test_status_says_when_first_seen_is_absent(ledger_dir, capsys):
    ledger.status()
    assert "stamped with today" in capsys.readouterr().out, (
        "an absent ledger is the bug, and must not read as a clean slate")
