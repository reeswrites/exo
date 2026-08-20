"""Backup — the store is load-bearing now, so it needs a restore path.

Snapshots the record's own materializations (zones + catalog + snapshots) into
backups/<stamp>/. That is a snapshot, not a backup: it sits on the same disk, so
it survives a bad rebuild and nothing else.

What it deliberately does NOT copy, and where each of those actually lives
(ADR-0018):

  notes/     the ONE tree with no upstream — once an export has been landed,
             that markdown is the only original. It belongs in git, committed
             with items/, exo.toml and serve-manifest.json, and off-machine in
             the object-storage mirror. Copying it beside the zones would put a
             second copy on the disk already holding the first.
  raw/       mirrors of somebody else's directory; `exo sync-raw` refills them.
  zones/t2   regenerable by definition; a rebuild is the restore.

The tested restore path is: clone the instance repo, export EXO_HOME, rebuild.
The raindrop snapshot IS kept here, because it is the one T0 pull with no
external record on disk to re-pull from.
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
