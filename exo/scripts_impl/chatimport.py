"""`wh chatimport` — one command for the whole chat-thread intake path.

A third-party chat export (claude.ai / chatgpt.com / Google AI Mode) downloaded to
`~/Downloads` has to travel a fixed path before it's queryable: land in the vault's
`chat-logs/`, get projected onto the ADR-0016 contract by second-brain's normalizer
(raw dump preserved under `chat-logs/source/`), sync into Exo's copy, then
re-project into `t0_chat`. This runs all of it.

Only files that ARE chat exports move. The signal is the exporter's frontmatter
`source:` — one of the known origins — NOT the `.md` extension, so an unrelated
markdown file in Downloads is left where it is (see `is_chat_export`).

    wh chatimport            # file every chat export in Downloads
    wh chatimport --dry-run  # list what would move; touch nothing
"""
from __future__ import annotations

import re
import shutil
import subprocess

from .. import config
from ..loaders import chat

# Mirrors capture/normalize.detect_origin's source set. iMessage is filename-detected
# there and never a Downloads export, so it's not here.
CHAT_ORIGINS = ("claude.ai", "chatgpt.com", "google-ai-mode")
_SOURCE_LINE = re.compile(r"^source:\s*(\S+)\s*$", re.M)


def is_chat_export(path) -> bool:
    """True iff the file's frontmatter names a known chat-export origin. Reads only the
    head — the frontmatter is the first thing in the file — so a huge note is cheap to
    reject."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:1500]
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    fm = head[3:end] if end != -1 else head
    m = _SOURCE_LINE.search(fm)
    return bool(m and m.group(1).strip().strip('"') in CHAT_ORIGINS)


def _find_exports() -> list:
    return sorted(p for p in config.DOWNLOADS.glob("*.md") if is_chat_export(p))


def run(dry: bool = False) -> int:
    exports = _find_exports()
    others = sorted(set(config.DOWNLOADS.glob("*.md")) - set(exports))
    print(f"chatimport: {len(exports)} chat export(s) in {config.DOWNLOADS}"
          f"{f', {len(others)} other .md left alone' if others else ''}")
    if not exports:
        print("  nothing to import")
        return 0
    for p in exports:
        print(f"  + {p.name}")
    if dry:
        print("\n--dry-run: nothing moved")
        return 0

    # 1. file them into the vault's chat-logs (the authoring home / source of truth)
    chat_logs = config.UPSTREAM_VAULT / "chat-logs"
    chat_logs.mkdir(parents=True, exist_ok=True)
    for p in exports:
        shutil.move(str(p), str(chat_logs / p.name))

    # 2. project onto the ADR-0016 contract via second-brain's normalizer, then assert
    #    every projection is gate-readable. Cross-repo, so shell into its own uv env.
    sb_root = config.UPSTREAM_VAULT.parent
    norm = ["uv", "run", "--project", "cli", "python", "cli/capture/normalize.py"]
    if subprocess.run(norm, cwd=sb_root).returncode != 0:
        print("  normalize FAILED — files are in the vault; fix and re-run normalize")
        return 1
    check = subprocess.run(norm + ["--check"], cwd=sb_root)
    if check.returncode != 0:
        print("  WARNING: gate check reported failures (see above)")

    # 3. pull the vault forward into Exo's copy, then re-project t0_chat
    from . import sync_raw
    sync_raw.sync_vault()
    chat.run()
    from .. import catalog
    names = catalog.build()
    print(f"  catalog: {len(names)} views rebuilt")
    return 0
