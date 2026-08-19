"""Backup — the store is load-bearing now, so it needs a restore path.

Snapshots the whole store (zones + catalog + snapshots) into backups/<stamp>/.
T1 notes themselves live in second-brain/git and are NOT copied here — the
runbook restores them from the vault. What we back up is the record's own
materializations + the raindrop snapshot (the one T0 pull without an external
record on disk).
"""
from __future__ import annotations

import shutil
from datetime import datetime

from .. import config

BACKUPS = config.ROOT / "backups"


def run() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUPS / stamp
    dest.mkdir(parents=True, exist_ok=True)
    # zones (parquet + raindrop snapshot). Skip _cache — it is droppable by
    # design (ADR-0002): a cache view is rebuilt by rerunning its engine, not
    # restored. _snapshots IS kept (the raindrop pull has no external record).
    shutil.copytree(
        config.ZONES, dest / "zones",
        dirs_exist_ok=True, ignore=shutil.ignore_patterns("_cache"),
    )
    if config.CATALOG.exists():
        (dest / "catalog").mkdir(exist_ok=True)
        shutil.copy2(config.CATALOG, dest / "catalog" / config.CATALOG.name)
    print(f"  backup -> {dest}")
    # prune: keep newest 10
    snaps = sorted(p for p in BACKUPS.iterdir() if p.is_dir())
    for old in snaps[:-10]:
        shutil.rmtree(old)
        print(f"  pruned {old.name}")
    return str(dest)
