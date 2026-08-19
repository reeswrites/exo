"""The engine may know the shape of a life, never the contents of one (ADR-0014).

This is the leak guard for the public split. It runs over the engine tree — the
package and the worker's source — and fails on anything that identifies *this*
instance: a home directory, the owner's name, his blog, a Cloudflare id.

The allowlist below is an exceptions ledger, not a config. It may only ever
shrink. Each entry names the leg that removes it, so a line that outlives its
leg is a visible debt rather than a permanent carve-out.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ("exo", "worker/src")
SUFFIXES = {".py", ".js", ".mjs", ".toml", ".json", ".sh"}

PATTERNS = {
    "home directory": re.compile(r"/Users/|/home/[a-z]"),
    "the owner": re.compile(r"\brees\b|reeswrites|reeshuffled", re.I),
    "a cloudflare id": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
}

# path -> why it is still here, and what removes it.
# Empty, and it should stay that way. An addition here is a debt with a leg
# number attached, not a place to park something.
ALLOWED: dict[str, set[str]] = {}


def _files():
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*")):
            if p.suffix not in SUFFIXES or "__pycache__" in p.parts or "node_modules" in p.parts:
                continue
            # This file spells the patterns out, so it matches itself. Skipping
            # it is not an exception to the rule — it IS the rule, written down.
            if p.resolve() == Path(__file__).resolve():
                continue
            yield p


def test_engine_names_no_instance():
    leaks = []
    for path in _files():
        rel = str(path.relative_to(ROOT))
        allowed = ALLOWED.get(rel, set())
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip() in allowed:
                continue
            for what, pat in PATTERNS.items():
                if pat.search(line):
                    leaks.append(f"{rel}:{n}: {what} — {line.strip()[:100]}")
    assert not leaks, "the engine names this instance:\n" + "\n".join(leaks)


def test_allowlist_only_shrinks():
    """An entry that no longer matches anything is a carve-out nobody removed."""
    stale = []
    for rel, lines in ALLOWED.items():
        body = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for line in lines:
            if line not in body:
                stale.append(f"{rel}: {line[:60]}")
    assert not stale, "allowlist entries no longer present — delete them:\n" + "\n".join(stale)
