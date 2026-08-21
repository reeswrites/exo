"""`publish --only` — the rules that stop a scoped run from deleting the record.

Three of these guard a single sentence from ADR-0015: **only a full bundle may
drop a table.** `import.sh` reconciles D1 against the bundle's table list, which
is correct for a nightly that carries everything and catastrophic for a lane
that carries one zone — it would drop every other table in production, on every
run, while reporting success.

The rest are about the other direction: a scoped run must not quietly publish
LESS than it claims, must not override the manifest, and must not carry stale
rows into the bundle behind fresher ones.
"""
from __future__ import annotations

import json

import pytest

from exo.scripts_impl import publish, publish_cf


# ─────────────────────── --only cannot widen policy ───────────────────────


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    m = {
        "zones": {"t0_music": "serve", "t1_notes": "serve", "t0_chat": "hold"},
        "note_folders": {}, "path_zones": {},
    }
    path = tmp_path / "serve-manifest.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(publish.config, "SERVE_MANIFEST", path)
    return m


def test_only_cannot_publish_a_held_zone(manifest, capsys):
    assert publish.run(only=["t0_chat"]) == 1
    out = capsys.readouterr().out
    assert "does not override policy" in out


def test_only_refuses_an_unknown_zone_rather_than_doing_nothing(manifest, capsys):
    # A typo that published nothing would look exactly like a run that worked.
    assert publish.run(only=["t0_muusic"]) == 1
    assert "not in serve-manifest.json" in capsys.readouterr().out


# ───────────────────── only a full bundle may drop ─────────────────────


def _bundle(tmp_path, scope, tables):
    out = tmp_path / "cf"
    (out / "data").mkdir(parents=True, exist_ok=True)
    publish_cf._emit_reconcile(out, {t: 1 for t in tables}, scope)
    return out


def test_a_partial_bundle_ships_no_reconcile_authority(tmp_path):
    out = _bundle(tmp_path, "partial", ["t0_music"])
    assert not (out / "served-tables.txt").exists(), (
        "served-tables.txt is what import.sh drops against; a scoped run must "
        "not ship one, or it deletes every zone it did not carry")
    assert (out / "bundle-tables.txt").read_text().split() == ["t0_music"]
    assert (out / "bundle-scope.txt").read_text().strip() == "partial"


def test_a_full_bundle_still_ships_one(tmp_path):
    out = _bundle(tmp_path, "full", ["t0_music", "t1_notes"])
    assert (out / "served-tables.txt").read_text().split() == ["t0_music", "t1_notes"]
    assert (out / "bundle-scope.txt").read_text().strip() == "full"


def test_the_scope_is_always_stated_so_neither_mode_is_the_absent_case(tmp_path):
    for scope in ("full", "partial"):
        out = _bundle(tmp_path / scope, scope, ["t0_music"])
        assert (out / "bundle-scope.txt").exists()


def test_the_guard_reads_the_bundles_own_table_list(tmp_path):
    # A partial bundle has no served-tables.txt, so a guard reading that file
    # would find nothing to check and wave a shrunken corpus straight through.
    out = _bundle(tmp_path, "partial", ["t0_music"])
    guard = (publish.config.ROOT.parent / "exo-me" / "scripts" / "guard-publication.sh")
    if not guard.exists():
        pytest.skip("the guard lives in the instance, not the engine")
    assert 'bundle-tables.txt"' in guard.read_text()


# ─────────────── import.sh refuses a bundle with no stated scope ───────────────


def test_import_refuses_an_unmarked_bundle():
    sh = publish_cf.IMPORT_SH
    assert 'SCOPE="$(cat "$HERE/bundle-scope.txt"' in sh
    assert "full|partial) ;;" in sh
    # The refusal must be the default arm, not a warning that falls through.
    scope_block = sh[sh.index('case "$SCOPE" in'):sh.index("LIVE=\"$(mktemp)\"")]
    assert "exit 1" in scope_block, (
        "an unrecognised scope must refuse: defaulting to full lets a lane's "
        "bundle wipe production, defaulting to partial stops the manifest from "
        "ever revoking a zone")


def test_a_full_import_still_drops_what_the_manifest_revoked_in_source():
    sh = publish_cf.IMPORT_SH
    assert 'grep -qxF "$t" "$HERE/served-tables.txt"' in sh
    assert "DROP TABLE IF EXISTS" in sh


# ───────────── what import.sh actually DOES, run against a stub ─────────────
#
# The rule "only a full bundle may drop a table" is shell control flow, not a
# string that is present or absent — the DROP lives in the full branch either
# way. Reading the source cannot tell whether the partial branch reaches it, and
# that question is the difference between a lane refreshing one zone and a lane
# deleting the record. So the script is run.

_STUB = """#!/bin/sh
# Fake wrangler: log every invocation, answer plausibly, never fail.
echo "$@" >> "$WRANGLER_LOG"
for a in "$@"; do
  case "$a" in
    "SELECT name FROM sqlite_master WHERE type='table'")
      # Pretty-printed, one "name" per line, because that is how wrangler --json
      # actually emits it AND because import.sh parses it with a per-line sed
      # whose leading .* is greedy: collapsed onto one line, only the last name
      # is ever seen and this test would pass while proving nothing.
      printf '%s\n' '[{"results":[' '{"name":"t0_music"},' '{"name":"t1_notes"},' '{"name":"wh_audit"}' ']}]'
      exit 0 ;;
  esac
done
case "$*" in
  *verify*|*"SELECT"*"count(*)"*) echo '[{"results":[{"t0_music":1,"t1_notes":1}]}]' ;;
esac
exit 0
"""


def _runnable_bundle(tmp_path, scope, tables):
    out = _bundle(tmp_path, scope, tables)
    (out / "schema.sql").write_text("-- schema\n", encoding="utf-8")
    for t in tables:
        (out / "data" / f"{t}.sql").write_text(f'DELETE FROM "{t}";\n', encoding="utf-8")
    stub = tmp_path / "wrangler"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return out, tmp_path / "calls.log"


def _run_import(out, log, extra_env=None):
    import os
    import subprocess
    env = {**os.environ, "WRANGLER": str(out.parent / "wrangler"),
           "WRANGLER_LOG": str(log), "PATH": f"{out.parent}:{os.environ['PATH']}"}
    env.update(extra_env or {})
    return subprocess.run(["sh", str(out / "import.sh"), "warehouse"],
                          capture_output=True, text=True, env=env, timeout=120)


def test_a_partial_import_drops_nothing(tmp_path):
    out, log = _runnable_bundle(tmp_path, "partial", ["t0_music"])
    proc = _run_import(out, log)
    calls = log.read_text() if log.exists() else ""
    assert "DROP TABLE" not in calls, (
        "a partial bundle carries one zone and knows nothing about the rest; "
        f"dropping against its list deletes production. stdout:\n{proc.stdout}")
    assert "dropping nothing" in proc.stdout


def test_a_partial_import_still_loads_its_own_table(tmp_path):
    out, log = _runnable_bundle(tmp_path, "partial", ["t0_music"])
    _run_import(out, log)
    assert "schema.sql" in log.read_text()


def test_a_full_import_does_reach_the_drop(tmp_path):
    # The stub reports a `wh_audit` table plus the two served ones. wh_ is
    # protected, so nothing should be dropped here either — but the run must go
    # through the reconcile rather than skip it.
    out, log = _runnable_bundle(tmp_path, "full", ["t0_music", "t1_notes"])
    proc = _run_import(out, log)
    assert "reconciling" in proc.stdout
    assert "sqlite_master" in log.read_text()


def test_a_full_import_drops_a_table_the_manifest_revoked(tmp_path):
    # t1_notes is live in D1 (the stub says so) but absent from the bundle:
    # exactly what tightening the manifest looks like.
    out, log = _runnable_bundle(tmp_path, "full", ["t0_music"])
    _run_import(out, log)
    assert 'DROP TABLE IF EXISTS "t1_notes"' in log.read_text()


def test_a_bundle_with_no_stated_scope_is_refused(tmp_path):
    out, log = _runnable_bundle(tmp_path, "full", ["t0_music"])
    (out / "bundle-scope.txt").unlink()
    proc = _run_import(out, log)
    assert proc.returncode == 1
    assert "refusing" in (proc.stdout + proc.stderr).lower()
    assert "DROP TABLE" not in (log.read_text() if log.exists() else "")


def test_a_bundle_with_a_junk_scope_is_refused(tmp_path):
    out, log = _runnable_bundle(tmp_path, "full", ["t0_music"])
    (out / "bundle-scope.txt").write_text("everything\n", encoding="utf-8")
    proc = _run_import(out, log)
    assert proc.returncode == 1
