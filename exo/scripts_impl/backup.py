"""Backup — the store is load-bearing now, so it needs a restore path.

Snapshots the record's own materializations (zones + catalog + snapshots) into
backups/<stamp>/. That is a snapshot, not a backup: it sits on the same disk, so
it survives a bad rebuild and nothing else.

What it deliberately does NOT copy, and where each of those actually lives
(ADR-0018):

  notes/     landed writing, treated as an original whether or not the silo it
             came from still has it — which is a question about a silo and not
             about this tree (ADR-0018). It belongs in git, committed with
             items/, exo.toml and serve-manifest.json, and off-machine in the
             object-storage mirror. Copying it beside the zones would put a
             second copy on the disk already holding the first.
  raw/       mirrors of somebody else's directory; `exo sync-raw` refills them.
  zones/t2   regenerable by definition; a rebuild is the restore.

The tested restore path is: clone the instance repo, export EXO_HOME, rebuild.
The raindrop snapshot IS kept here, because it is the one T0 pull with no
external record on disk to re-pull from.

`--out <dir>` writes a PORTABLE snapshot instead, for an instance to ship
wherever it keeps durable state — the engine does not know where that is
(ADR-0014). It differs from the local one in two ways that matter, both of them
about being restorable somewhere else: it carries no catalog (whose views name
absolute parquet paths, so a restored one points at the machine it was taken
on), and it writes a MANIFEST.json of per-zone row counts so a restore can be
CHECKED. Without that manifest a restore drill proves only that a tarball
unpacks (ADR-0015 §2, the warm copy).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .. import config, io

BACKUPS = config.ROOT / "backups"

# What a portable snapshot deliberately leaves out, and it is not the same list
# as the local one.
#
# The CATALOG. `catalog._register` builds every view as
# `read_parquet('<absolute path>')`, so a catalog restored to a different
# machine — or the same machine at a different path — points at somewhere that
# is not there. It reads as a working database and answers nothing. `exo build`
# regenerates it from the parquet in seconds, which is why the local snapshot
# can carry it (same path, so the views still resolve) and this cannot.
#
# `_cache/` is droppable by design (ADR-0002): a cache view is rebuilt by
# rerunning its engine, not restored.
_SKIP_LOCAL = ("_cache",)


def _counts(root: Path) -> dict[str, int]:
    """Rows per zone, so a restore can be CHECKED rather than assumed.

    This is the difference between a backup and a claim. A restore that
    produces a catalog and no assertion about what is in it has proved that a
    tarball unpacks.
    """
    out: dict[str, int] = {}
    for tier in ("t0", "t1", "t2"):
        for parquet in sorted((root / "zones" / tier).glob("*.parquet")):
            out[f"{tier}_{parquet.stem}"] = io.row_count(parquet)
    return out


def portable(dest: str) -> int:
    """A snapshot that can be restored somewhere else — for `--out`.

    Zones and the ledger, no catalog. Nothing is pruned: this writes into a
    directory the caller named, and deleting siblings in someone else's
    directory is not a backup's business.
    """
    out = Path(dest).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        config.ZONES, out / "zones",
        dirs_exist_ok=True, ignore=shutil.ignore_patterns(*_SKIP_LOCAL),
    )
    counts = _counts(out)
    (out / "MANIFEST.json").write_text(json.dumps({
        "taken_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "zones": counts,
        "rows_total": sum(counts.values()),
        "catalog": ("absent by design — catalog views bake absolute parquet paths, "
                    "so a restored one points at the machine it was taken on. "
                    "Run `exo build`."),
    }, indent=1, sort_keys=True), encoding="utf-8")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"  backup -> {out}  ({len(counts)} zones, {sum(counts.values()):,} rows, "
          f"{size / 1_000_000:.1f}MB)")
    return 0


def run() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUPS / stamp
    dest.mkdir(parents=True, exist_ok=True)
    # zones (parquet + raindrop snapshot). Skip _cache — it is droppable by
    # design (ADR-0002): a cache view is rebuilt by rerunning its engine, not
    # restored. _snapshots IS kept (the raindrop pull has no external record).
    shutil.copytree(
        config.ZONES, dest / "zones",
        dirs_exist_ok=True, ignore=shutil.ignore_patterns(*_SKIP_LOCAL),
    )
    # Kept here and NOT in `portable`: this snapshot sits at the same path the
    # catalog's views name, so restoring it in place still resolves.
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
