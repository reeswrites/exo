"""`exo init` — scaffold an instance.

The engine holds no data, so the first thing a new user needs is somewhere for
it to go. This writes that somewhere: a config to edit, a publication policy to
decide, the directories the loaders expect, and a .gitignore that assumes what
is inside is private.

It writes the policy file as a *copy of the example* rather than a working
default on purpose. `serve-manifest.json` is the only thing standing between a
record and the internet, and an inherited policy is not a decision — every line
of it should be read once by the person it publishes.
"""
from __future__ import annotations

import shutil
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

DIRS = [
    "raw/exports", "raw/posts", "raw/vault", "raw/drafts", "raw/inventory",
    "zones/t0", "zones/t1", "zones/t2", "zones/_cache", "zones/_ledger",
    "zones/_snapshots", "catalog", "items", "notes", "captures", "plugins",
]

GITIGNORE = """\
# An instance is private. Everything here is either your data or derived from it.
#
# What is NOT ignored, and should be committed: exo.toml, serve-manifest.json,
# plugins/, and items/ — your config, your publication policy, your own loaders
# and your authored task spine. Those are the parts worth version history.

# Anchored with a leading slash, deliberately: an unanchored `raw/` matches at
# any depth and will quietly swallow a directory of the same name nested
# somewhere you meant to keep.

# derived — rebuildable with `exo rebuild`
/zones/t0/*.parquet
/zones/t1/*.parquet
/zones/t2/*.parquet
/zones/_cache/*.parquet
/zones/_serve/
/zones/_ledger/
/catalog/*.duckdb
/catalog/*.duckdb.wal

# inputs and records: large, and yours
/raw/
/captures/
/notes/
/backups/

.venv/
__pycache__/
*.pyc
.DS_Store
"""

NEXT = """\
Instance created at {dest}

  1. Edit exo.toml — who you are, and where your inputs live.
     owner.voice and vault.about_me_folders are stored in every derived id.
     Set them now; changing them later re-mints the whole derived tier.

  2. Read serve-manifest.json line by line and decide it. It is fail-closed:
     anything unlisted fails the build rather than defaulting either way.

  3. Put your exports in raw/exports/ — a Last.fm CSV, a Letterboxd export,
     a Goodreads dump. Point [paths].vault at a folder of markdown.

  4. export EXO_HOME={dest}
     exo rebuild
     exo publish --dry-run
"""


def run(dest_arg: str) -> int:
    dest = Path(dest_arg).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"✗ {dest} exists and is not empty — refusing to scaffold over it")
        return 1
    for d in DIRS:
        (dest / d).mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEMPLATES / "exo.toml", dest / "exo.toml")
    shutil.copy2(TEMPLATES / "serve-manifest.json", dest / "serve-manifest.json")
    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dest / "plugins" / ".gitkeep").write_text("", encoding="utf-8")
    print(NEXT.format(dest=dest))
    return 0
