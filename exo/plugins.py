"""Plugin discovery — how an instance adds sources the engine has never heard of.

The engine ships the loaders whose input is a *format*: a Last.fm export, a
Letterboxd CSV, a markdown vault, a folder of git repos. Anyone can hold one of
those. The loaders whose input is a *place* — one city's venues, one person's
siblings, one blog's precomputed vectors — belong to the instance that has that
place, and live in `$EXO_HOME/plugins/` (ADR-0014 §3).

A plugin is a module or package under `plugins/` that may declare any of:

    LOADERS      = {"event": fn}      zone name -> zero-arg fn -> list[Row]
    T1_LOADERS   = {"taste": fn}      the same, for the authored tier
    DERIVATIONS  = {"name": fn}       T2, read through the wall only
    COMMANDS     = {"fetch-ra": (fn, "one-line help")}
    SYNCS        = {"taste": fn}      mirror an upstream into raw/, on sync-raw

Two rules make the failure modes legible:

**A zone may have several contributors, and they merge.** `event` is one zone
fed by five pullers; a plugin that declares an existing zone name adds to it
rather than replacing it, and ingest prints each contribution separately. A
replace rule would let a new plugin silently empty a zone that four other
loaders were filling.

**A plugin that cannot be imported is an error, never a skip.** A loader that
vanishes quietly rebuilds a smaller corpus with every step green — the exact
failure the publication guard exists to catch downstream. Fail here, loudly,
where the cause is still visible.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from . import config

PLUGINS = config.ROOT / "plugins"

_cache: list | None = None


def _load_module(path: Path):
    name = "exo_plugin_" + path.stem if path.is_file() else "exo_plugin_" + path.name
    if name in sys.modules:
        return sys.modules[name]
    target = path if path.is_file() else path / "__init__.py"
    spec = importlib.util.spec_from_file_location(name, target, submodule_search_locations=(
        None if path.is_file() else [str(path)]
    ))
    if spec is None or spec.loader is None:
        raise ImportError(f"plugin {path} has no loadable spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # set before exec: a package's own submodules import it
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[name]
        raise
    return mod


def discover() -> list:
    """Every plugin module, in a stable order. Cached for the process."""
    global _cache
    if _cache is not None:
        return _cache
    mods = []
    if PLUGINS.is_dir():
        for p in sorted(PLUGINS.iterdir()):
            if p.name.startswith(("_", ".")):
                continue
            if p.is_file() and p.suffix == ".py":
                mods.append(_load_module(p))
            elif p.is_dir() and (p / "__init__.py").exists():
                mods.append(_load_module(p))
    _cache = mods
    return mods


def _collect(attr: str) -> dict:
    """Flatten every plugin's dict into zone -> [(who, fn)].

    A value may be one callable or a list of them. The list form exists so a
    plugin that feeds one zone from several places can be *counted* separately:
    `event` has four pullers, and a single merged lambda would report one total
    that cannot show which puller went to zero.
    """
    out: dict = {}
    for mod in discover():
        for k, v in getattr(mod, attr, {}).items():
            fns = v if isinstance(v, (list, tuple)) else [v]
            for fn in fns:
                who = getattr(fn, "__module__", mod.__name__).rsplit(".", 1)[-1]
                out.setdefault(k, []).append((who, fn))
    return out


def loaders() -> dict[str, list[tuple[str, Callable]]]:
    """zone -> [(plugin name, loader)]. Several contributors per zone is normal."""
    return _collect("LOADERS")


def derivations() -> dict[str, list[tuple[str, Callable]]]:
    return _collect("DERIVATIONS")


def commands() -> dict[str, tuple[Callable, str]]:
    """CLI name -> (fn, help). Last plugin wins; a collision is a naming bug."""
    out: dict[str, tuple[Callable, str]] = {}
    for mod in discover():
        for name, spec in getattr(mod, "COMMANDS", {}).items():
            fn, help_text = spec if isinstance(spec, tuple) else (spec, "")
            out[name] = (fn, help_text)
    return out


def t1_loaders() -> dict[str, list[tuple[str, Callable]]]:
    """zone -> [(plugin name, loader)] for T1.

    A separate dict from `LOADERS` rather than a tier key inside it, because the
    tier is the write-scope — the one thing about a loader that is not a detail.
    A plugin that meant to write consumption data and typed T1 by accident would
    be claiming its rows are the owner's own words.
    """
    return _collect("T1_LOADERS")


def syncs() -> dict[str, list[tuple[str, Callable]]]:
    """name -> [(plugin, fn)] run by `exo sync-raw`.

    A loader whose input is a place usually has a *sync* behind it — the step
    that mirrors somebody else's directory into raw/ before anything reads it.
    Moving the loader out and leaving the sync in the engine would keep the
    engine knowing which sibling repos exist on one particular laptop, which is
    the thing the split is for.
    """
    return _collect("SYNCS")
