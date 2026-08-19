"""Path authority — the only module that knows where things live on disk.

Everything else asks config for a path. Nothing here writes; it only resolves.

Three layers, outermost wins:

    environment variable  →  exo.toml  →  built-in default

The environment layer exists so CI can repoint a source without editing a file
the laptop owns. The toml layer exists so that *this* installation's facts —
whose blog, which folder the exports land in, where the vault is checked out —
live in the instance rather than in the engine. The built-in default exists so a
fresh instance runs before anyone has written a config at all.

The instance is found by EXO_HOME, or by walking up from the working directory
looking for `exo.toml`. That walk is what lets the engine be installed from a
wheel while the record it reads stays somewhere else entirely.
"""
from __future__ import annotations

import glob
import os
import tomllib
from pathlib import Path

# ────────────────────────────── the instance ──────────────────────────────


def _find_home() -> Path:
    """Where this instance's record lives.

    EXO_HOME wins. Otherwise walk up from the cwd for an `exo.toml`, which is
    what makes `exo query ...` work from anywhere inside the instance. Falling
    back to the package's parent keeps the pre-split layout (engine and record
    in one checkout) working unchanged.
    """
    env = os.environ.get("EXO_HOME") or os.environ.get("WH_HOME")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path.cwd().resolve()
    for p in (cur, *cur.parents):
        if (p / "exo.toml").exists():
            return p
    return Path(__file__).resolve().parent.parent


ROOT = _find_home()


def _load_toml() -> dict:
    f = ROOT / "exo.toml"
    if not f.exists():
        return {}
    with f.open("rb") as fh:
        return tomllib.load(fh)


_CFG = _load_toml()


def setting(section: str, key: str, default=None):
    """One value from exo.toml, or the built-in default."""
    return _CFG.get(section, {}).get(key, default)


def _env(*names: str) -> str | None:
    """First environment variable set among `names`.

    Two names because every EXO_* variable answers to its WH_* predecessor for
    one release. A rename that silently ignores an override the caller believes
    is in effect is worse than one that never happened.
    """
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def _path(env_name: str, section: str, key: str, default: str, *legacy: str) -> Path:
    """Resolve one path through all three layers.

    Relative values resolve against the instance root, so an instance can be
    moved or checked out anywhere without editing its own config.

    `legacy` names the pre-Exo variables that meant this path under a different
    spelling. They are not politeness: the nightly sets WH_UPSTREAM_SECOND_BRAIN
    to point CI at the checked-out vault, and an override that is silently
    ignored does not fail — it rebuilds a smaller corpus with every step green.
    """
    raw = _env(env_name, "WH_" + env_name.removeprefix("EXO_"), *legacy) \
        or setting(section, key) or default
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def _optional(env_name: str, section: str, key: str, *legacy: str) -> Path | None:
    """A path an instance may simply not have.

    Returns None rather than a Path when nothing is configured, because the
    empty string is NOT a harmless default here: Path("") is Path("."), which
    is relative, which resolves to the instance root — a root that exists, so
    every "does this upstream exist?" check passes and `sync-raw` mirrors the
    instance INTO ITS OWN raw/ with rsync --delete. An instance that omitted a
    section it does not have would have its raw/ rewritten by its own contents.
    """
    raw = _env(env_name, "WH_" + env_name.removeprefix("EXO_"), *legacy) or setting(section, key)
    if not raw:
        return None
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else (ROOT / p)


# ────────────────────────────── the record ──────────────────────────────
# Engine-owned layout. Not configurable: these ARE the store (ADR-0001), and an
# instance that moved them would no longer be the thing the wall is a property of.

ZONES = ROOT / "zones"
T0 = ZONES / "t0"
T1 = ZONES / "t1"
T2 = ZONES / "t2"
CACHE = ZONES / "_cache"  # cache namespace (ADR-0002): joinable, disposable, NOT verify-bound
CATALOG = ROOT / "catalog" / "exo.duckdb"
SNAPSHOTS = ZONES / "_snapshots"  # raw pulls (raindrop) kept for reproducibility
EMBED_CACHE = CACHE / "embed.parquet"  # content-addressed vectors
LEDGER = ZONES / "_ledger"  # durable ingestion facts; survives every rebuild
FIRST_SEEN = LEDGER / "first_seen.parquet"  # id -> when this store first saw the row
SERVE = ZONES / "_serve"  # publication projection: the ONLY bytes that leave (ADR-0005)
SERVE_MANIFEST = ROOT / "serve-manifest.json"  # fail-closed serve/hold policy
CAPTURES = ROOT / "captures"  # reversible captures (Life Terminal 'save to brain')
NOTES = ROOT / "notes"  # the engine's OWN note record (Apple Notes ingester writes here)
ITEMS = ROOT / "items"  # item spine (ADR-0002): authored .md nouns + _events log

# Where the laptop leg records what it last did. Written by an instance's own
# sync script, read by the brief so a reader can see its own age — so the path
# is an instance fact, not an engine one, and the default is only a default.
STATE = _path("EXO_STATE", "paths", "state", "~/.local/state/exo")

SECRETS = Path.home() / ".config" / "exo" / "secrets"
ENV_FILE = SECRETS / "env"  # KEY=VALUE, mode 600, never in the repo
LEGACY_ENV_FILE = Path.home() / ".config" / "warehouse" / "secrets" / "env"


# ────────────────────────────── who ──────────────────────────────

OWNER = setting("owner", "name", "you")
OWNER_POSSESSIVE = setting("owner", "possessive", "their")
OWNER_VOICE = setting("owner", "voice", "owner")  # atom attribution label
BLOG_URL_TEMPLATE = _env("EXO_BLOG_URL_TEMPLATE") or setting("blog", "url_template", "")


def post_url(slug: str) -> str:
    """The live URL of a published post, or "" when this instance has no blog.

    The right answer about a published post is often the link rather than a
    paraphrase, so the template is the one blog fact the engine cannot infer.
    """
    return BLOG_URL_TEMPLATE.format(slug=slug) if BLOG_URL_TEMPLATE else ""


# ────────────────────────────── colocated inputs ──────────────────────────────
# Physically held by this instance (ADR-0001). The consumption exports, the
# mirrored blog, the mirrored vault, the drafts.

EXPORTS = _path("EXO_EXPORTS", "paths", "exports", "raw/exports")
POSTS = _path("EXO_POSTS", "paths", "posts", "raw/posts")
VAULT = _path("EXO_VAULT", "paths", "vault", "raw/vault", "WH_SECOND_BRAIN_DATA")
DRAFTS = _path("EXO_DRAFTS", "paths", "drafts", "raw/drafts")
# Hand-written procedures — how the owner does a recurring thing. NOT under raw/:
# nothing mirrors them and no loader derives them, so they are authored material
# this instance holds first-hand, like items/.
PROCEDURES = _path("EXO_PROCEDURES", "paths", "procedures", "procedures")
INVENTORY = _path("EXO_INVENTORY", "paths", "inventory", "raw/inventory")
MEDIA_EMBEDDINGS = _path("EXO_MEDIA_EMBEDDINGS", "paths", "media_embeddings", "raw/media_embeddings.json")
PROJECTS_SNAPSHOT = _path("EXO_PROJECTS_SNAPSHOT", "paths", "projects_snapshot", "raw/projects.json")
DOWNLOADS = _path("EXO_DOWNLOADS", "paths", "downloads", "~/Downloads")

# ────────────────────────────── MUST-STAY upstreams ──────────────────────────────
# Another system owns these (a published blog, a live vault), so the engine
# SYNCs copies into the colocated paths above via `exo sync-raw` rather than
# owning them. An absent upstream is skipped; a viewer must not fail an ingest.

UPSTREAM_POSTS = _optional("EXO_UPSTREAM_POSTS", "upstream", "posts")
UPSTREAM_VAULT = _optional("EXO_UPSTREAM_VAULT", "upstream", "vault", "WH_UPSTREAM_SECOND_BRAIN")
UPSTREAM_DRAFTS = _optional("EXO_UPSTREAM_DRAFTS", "upstream", "drafts")

# garden/ is authoring-only — no loader reads it — so it is excluded from the
# vault sync. Same for the vault's own .git and OS cruft.
VAULT_SYNC_EXCLUDES = ("garden/", ".git/", ".DS_Store", "*.bak", ".*.bak")

# The one WRITE direction: the engine owns every event puller and regenerates
# that repo's feed page. Absent checkout = the page is skipped, never an error.
DC_EVENTS_WEB = _optional("EXO_DC_EVENTS_WEB", "write", "dc_events_web")

# ────────────────────────────── the workshop ──────────────────────────────
# The owner's repos, scanned on the laptop by `exo scan-projects` into a
# snapshot, so CI rebuilds the zones without needing 40 checkouts (ADR-0011).

PROJECTS_ROOT = _path("EXO_PROJECTS_ROOT", "projects", "root", "..")
# Top-level directory names never treated as project space. Not a privacy list —
# these hold no authored work: vendored deps, a recorder's dumping ground.
# Privacy is scan-time exclusion (EXO_PROJECTS_DENY), which is a different list.
PROJECTS_DENY = tuple(
    d for d in (
        _env("EXO_PROJECTS_DENY", "WH_PROJECTS_DENY")
        or ",".join(setting("projects", "deny", ["node_modules"]))
    ).split(",") if d
)
# How deep under the root a .git may sit and still count. 2 catches hiatus/<repo>.
PROJECTS_MAX_DEPTH = int(
    _env("EXO_PROJECTS_MAX_DEPTH", "WH_PROJECTS_MAX_DEPTH")
    or setting("projects", "max_depth", 2)
)


# ────────────────────────────── credentials ──────────────────────────────


def load_env() -> int:
    """Load ingestion credentials from the secrets dir into os.environ.

    Handles both spellings: `KEY=value` and the `export KEY=value` a shell
    needs, since the same file is sourced by the laptop's sync script.

    Parsed here rather than sourced by a shell. `set -a; . .env` aborts on the
    first value containing a shell metacharacter — an unquoted `&` in a blocklist
    silently truncated everything below it — and a credential loader that stops
    halfway without saying so is worse than one that is missing.

    Existing environment wins, so CI secrets are never overridden by a laptop file.
    """
    n = 0
    for f in (ENV_FILE, LEGACY_ENV_FILE):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            # `export KEY=value`, because the same file is also sourced by the
            # laptop's sync script and a shell needs the keyword. Without this
            # the loader stored a variable literally named "export KEY", every
            # lookup missed, and a credentialed fetch reported the key as unset
            # while it sat in the file being read — the failure looked like a
            # missing secret rather than a parser that could not read one.
            key = key.strip().removeprefix("export").strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
                n += 1
    return n


# ────────────────────────────── helpers ──────────────────────────────


def latest(pattern: str, base: Path = EXPORTS) -> Path | None:
    """Newest file matching a glob under an input dir.

    Exports are dated (lastfm-2026-06-02.csv); we always want the freshest.
    """
    hits = sorted(glob.glob(str(base / pattern)))
    return Path(hits[-1]) if hits else None


def ensure_dirs() -> None:
    for d in (T0, T1, T2, CACHE, CATALOG.parent, SNAPSHOTS, LEDGER):
        d.mkdir(parents=True, exist_ok=True)


def item_dirs() -> dict[str, Path]:
    """The item-spine subdirs, created on demand (ADR-0002)."""
    dirs = {
        "task": ITEMS / "task",
        "habit": ITEMS / "habit",
        "slot": ITEMS / "slot",
        "constraint": ITEMS / "constraint",
        "events": ITEMS / "_events",
        "glance": ITEMS / "_glance",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs
