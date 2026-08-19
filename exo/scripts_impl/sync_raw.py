"""`wh sync-raw` — refresh Exo's local copies of MUST-STAY upstreams.

NOT the collection inventories. Those were mirrored here from the blog repo's
`_data/inventory/`, which is that repo's *output* of a Google Sheets fetch — so
the store sat a hop downstream of the truth and went stale whenever that repo did
not run. `wh fetch-collections` now pulls the sheets directly and writes the same
files. Mirroring as well would overwrite a fresh fetch with a stale copy, which
is the exact failure this note exists to prevent.

Most raw data is Exo-OWNED: it physically lives in `raw/` and nothing outside
writes it (the blog reads its input FROM here now). But two sources cannot be owned
by the store, because another live system owns them:

  - the blog's `_posts/`  — the PUBLISHED Jekyll blog builds from it in place.
  - second-brain's `data/`   — the LIVE authoring vault (sync/atomize/tend write it),
                                guarded by its own raw-write hook, until dissolution.

For those, Exo keeps a colocated COPY in `raw/` and pulls the upstream forward
here before an ingest. `rsync --delete` so the copy is a faithful mirror (a post
deleted upstream disappears here too). The vault's `garden/` (51M, authoring-only, no
loader reads it) is excluded — see config.VAULT_SYNC_EXCLUDES.

This is the transitional shape. When second-brain is dissolved (its writers + raw-write
guard rehomed into Exo), UPSTREAM_SECOND_BRAIN goes away and the vault is simply
Exo-owned like everything else — at which point this sync becomes a no-op for it.

    wh sync-raw            # mirror both upstreams into raw/
    wh sync-raw --posts    # just the blog
    wh sync-raw --vault    # just the second-brain vault
"""
from __future__ import annotations

import subprocess

from .. import config, plugins


def mirror(src: str, dst: str, excludes: tuple[str, ...] = ()) -> int:
    """Mirror src/ -> dst/ (trailing slash = contents). --delete keeps it faithful.

    Public because a plugin's sync needs exactly this and should not reimplement
    it: --delete is unforgiving, and one careful copy of that call is safer than
    several careless ones.
    """
    config.POSTS.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete"]
    for e in excludes:
        cmd += ["--exclude", e]
    cmd += [src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  rsync FAILED ({src} -> {dst}):\n{proc.stderr}")
    return proc.returncode


def sync_posts() -> int:
    src, dst = config.UPSTREAM_POSTS, config.POSTS
    if src is None:
        print("  posts: no upstream configured — skipped")
        return 0
    if not src.exists():
        print(f"  posts: upstream missing ({src}) — skipped")
        return 0
    rc = mirror(str(src), str(dst))
    if rc == 0:
        n = len(list(dst.glob("*.md")))
        print(f"  posts  {n:>6,} files   <- {src}")
    return rc


def sync_vault() -> int:
    src, dst = config.UPSTREAM_VAULT, config.VAULT
    if src is None:
        print("  vault: no upstream configured — skipped")
        return 0
    if not src.exists():
        print(f"  vault: upstream missing ({src}) — skipped")
        return 0
    rc = mirror(str(src), str(dst), config.VAULT_SYNC_EXCLUDES)
    if rc == 0:
        chats = len(list((dst / "chat-logs").glob("*.md"))) if (dst / "chat-logs").exists() else 0
        raws = sum(1 for _ in dst.glob("raw/**/*.md"))
        print(f"  vault  raw={raws:,} chat-logs={chats:,}   <- {src} (garden/ excluded)")
    return rc


def sync_drafts() -> int:
    """md-writer's drafts. Mirrored, not owned: md-writer writes and commits it.

    Unlike the blog, this repo has no remote — the writing is not public — so CI
    cannot check it out and it rides in the raw tarball instead.
    """
    src, dst = config.UPSTREAM_DRAFTS, config.DRAFTS
    if src is None:
        print("  drafts: no upstream configured — skipped")
        return 0
    if not src.exists():
        print(f"  drafts: upstream missing ({src}) — skipped")
        return 0
    rc = mirror(str(src), str(dst), (".git/", "*.md.tmp", ".DS_Store"))
    if rc == 0:
        n = len([f for f in dst.glob("*.md") if f.name != "README.md"])
        print(f"  drafts {n:>7,} files   <- {src}")
    return rc


def run(posts: bool = True, vault: bool = True) -> int:
    print("sync-raw: pulling MUST-STAY upstreams into raw/")
    rc = 0
    if posts:
        rc |= sync_posts()
    if vault:
        rc |= sync_vault()
    rc |= sync_drafts()
    # Whatever this instance mirrors that the engine has never heard of. A
    # loader whose input is a place usually has a sync behind it, and leaving
    # that sync here would keep the engine holding one laptop's directory
    # layout (ADR-0014 §3).
    for name, contributors in sorted(plugins.syncs().items()):
        for who, fn in contributors:
            rc |= fn() or 0
    return rc
