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
# plugins/, items/ and notes/ — your config, your publication policy, your own
# loaders, your authored task spine and your notes. Those are the parts worth
# version history.

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
/backups/

# notes/ is deliberately NOT here (ADR-0018). Everything else ignored above has
# an upstream: raw/ mirrors somebody else's directory, zones/ rebuild, backups/
# are copies. notes/ is the one that does not — once `exo ingest-notes` has
# landed a Notion export or an Apple Notes database, that tree is the only
# original, and the silo it came out of may be gone or may have lost the note.
# It is markdown, so git holds it for nothing and diffs it usefully.

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

     For notes out of a silo, declare a source in exo.toml and import it:
       exo ingest-notes notion --from ~/Downloads/Export-....zip
       exo ingest-notes files  --from ~/writing
     They land in notes/raw/<source>/, and the first publish afterwards will
     refuse until you decide their path_zone. That refusal is the design.

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
