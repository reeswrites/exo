"""The portable snapshot — the warm copy's contents, and why they differ.

A backup nobody has restored is a claim. Two things here are what turn this one
into a guarantee: it refuses to carry the catalog (which cannot survive the
journey), and it writes down what it holds so a restore can be checked rather
than assumed.
"""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from exo.scripts_impl import backup


@pytest.fixture
def instance(tmp_path, monkeypatch):
    zones = tmp_path / "zones"
    for tier in ("t0", "t1", "t2"):
        (zones / tier).mkdir(parents=True)
    (zones / "_cache").mkdir()
    (zones / "_ledger").mkdir()
    pq.write_table(pa.table({"id": ["a", "b", "c"]}), zones / "t0" / "music.parquet")
    pq.write_table(pa.table({"id": ["x"]}), zones / "t1" / "notes.parquet")
    pq.write_table(pa.table({"id": ["j"] * 9}), zones / "_cache" / "junk.parquet")
    (zones / "_ledger" / "first_seen.parquet").write_bytes(b"ledger")
    catalog = tmp_path / "catalog" / "exo.duckdb"
    catalog.parent.mkdir()
    catalog.write_bytes(b"duckdb")
    monkeypatch.setattr(backup.config, "ZONES", zones)
    monkeypatch.setattr(backup.config, "CATALOG", catalog)
    monkeypatch.setattr(backup.config, "ROOT", tmp_path)
    return tmp_path


def test_the_portable_snapshot_carries_no_catalog(instance, tmp_path):
    # catalog._register writes views as read_parquet('<absolute path>'), so a
    # restored catalog points at the machine it was taken on. It reads as a
    # working database and answers nothing — worse than an absent one.
    out = tmp_path / "out"
    backup.portable(str(out))
    assert not (out / "catalog").exists()
    assert "exo build" in json.loads((out / "MANIFEST.json").read_text())["catalog"]


def test_the_local_snapshot_still_carries_it(instance):
    # Same path, so the views still resolve — which is exactly why the local
    # one may keep it and the portable one may not.
    dest = backup.run()
    from pathlib import Path
    assert (Path(dest) / "catalog" / "exo.duckdb").exists()


def test_the_ledger_travels(instance, tmp_path):
    # The one state a rebuild cannot regenerate. A snapshot without it restores
    # a record that has forgotten when it first saw anything.
    out = tmp_path / "out"
    backup.portable(str(out))
    assert (out / "zones" / "_ledger" / "first_seen.parquet").exists()


def test_the_disposable_cache_does_not(instance, tmp_path):
    out = tmp_path / "out"
    backup.portable(str(out))
    assert not (out / "zones" / "_cache").exists()


def test_row_counts_are_recorded_so_a_restore_can_be_checked(instance, tmp_path):
    out = tmp_path / "out"
    backup.portable(str(out))
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["zones"] == {"t0_music": 3, "t1_notes": 1}
    assert manifest["rows_total"] == 4
    assert "t2" not in " ".join(manifest["zones"]), "empty tiers contribute nothing"


def test_the_cache_is_not_counted_as_record(instance, tmp_path):
    out = tmp_path / "out"
    backup.portable(str(out))
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert 9 not in manifest["zones"].values(), (
        "a cache zone in the counts would make a restore assert against rows "
        "the snapshot deliberately does not carry")


def test_portable_prunes_nothing(instance, tmp_path):
    # It writes into a directory the caller named. Deleting siblings there is
    # not a backup's business.
    out = tmp_path / "out"
    (out).mkdir()
    (out / "someone-elses-file").write_text("keep me", encoding="utf-8")
    backup.portable(str(out))
    assert (out / "someone-elses-file").exists()
