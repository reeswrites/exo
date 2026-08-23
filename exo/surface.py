"""Which tools this instance offers — resolved at publish time, shipped as data.

The engine ships one tool table (`worker/src/tools.js`). An instance is not
obliged to offer all of it, and ADR-0020 gives it two reasons not to:

  a held dependency   Flipping `t1_recipe` to hold used to leave `recipes`
                      advertised, and calling it raised a D1 "no such table"
                      that the worker reported as `tool failed`. A configuration
                      choice surfaced to the caller as a malfunction.

  a peer that does it better   An assistant holding this surface AND the
                      workspace's own MCP server has two ways to read the same
                      note. Two tools answering one question is not redundancy
                      that costs nothing; it is a caller choosing between them
                      with no basis, and an owner reading the same paragraph
                      twice in one answer.

## The resolution happens HERE, not on the surface

Same rule the exposure axis follows: `publish` decides and the worker reads. A
default chosen in two places is a default that will eventually differ, and the
worker cannot see `exo.toml` at all. So this module resolves the tool list down
to names and `publish` writes them into the bundle.

## And it fails OPEN, which is the opposite of everything around it

Every other gate in this codebase fails closed, because forgetting one leaks.
This one does not, and the asymmetry is deliberate rather than an oversight:

  - Data is gated by physical omission. A held zone is NOT IN the projection, so
    a tool that reaches for it finds nothing whether or not it was advertised.
    The tool list is an ergonomic claim about what is worth calling; it is not
    what keeps anything private, and nothing here can widen what leaves.

  - Failing closed here means a missing `surface.json` empties the surface. An
    engine that added a tool would make it invisible until every instance opted
    in by name — which is precisely the "publishing is not offering" failure
    ADR-0013 named as a STANDING DUTY, after `t1_item` sat in production for
    weeks with no tool over it.

So: an unreadable tool list means every tool the engine defines, and the worker
carries the same rule for the same reason.
"""
from __future__ import annotations

from . import config, toolzones

# Why a tool is not offered. Ordered by precedence — the first that applies is
# the one reported, because "you turned it off" is a more useful thing to read
# than "and also its zones are held".
WITHHELD_DISABLED = "disabled"
WITHHELD_DOMAIN = "domain"
WITHHELD_ZONES = "zones"


class UnknownToolInConfig(ValueError):
    """`[tools] disable` names something the surface does not define.

    Loud rather than ignored. A typo in a deny-list silently offers the tool the
    owner believed they had turned off, and the whole point of the list is that
    somebody decided against it.
    """


def config_tools() -> tuple[list[str], list[str]]:
    """`[tools]` from exo.toml: what to switch off, and which domains to keep."""
    return (
        list(config.setting("tools", "disable", []) or []),
        list(config.setting("tools", "domains", []) or []),
    )


def peers() -> dict[str, str]:
    """`[peers]` from exo.toml: note `source` -> the MCP server that serves it live.

    A peer is not a competitor. It means: rows carrying this source exist in a
    system the caller can also reach, so an answer that repeats them verbatim is
    the same paragraph twice. Exo states the fact — provenance and an id — and
    what to do about it belongs to the agent (ADR-0013 §2).
    """
    return {str(k): str(v) for k, v in (config.setting("peers", "sources", {}) or {}).items()}


def resolve(served: set[str] | frozenset[str],
            *, disable: list[str] | None = None,
            domains: list[str] | None = None) -> dict:
    """The tool list this instance offers, plus why each absentee is absent.

    `served` is the set of zone names that actually reached the projection —
    which is what `publish` has in hand, and is a stronger fact than the
    manifest: a zone can be marked serve and still be absent because it has no
    rows yet.
    """
    if disable is None or domains is None:
        cfg_disable, cfg_domains = config_tools()
        disable = cfg_disable if disable is None else disable
        domains = cfg_domains if domains is None else domains

    unknown = sorted(set(disable) - set(toolzones.TOOL_ZONES))
    if unknown:
        raise UnknownToolInConfig(
            f"[tools] disable names {unknown}, which no tool answers to. "
            f"Known tools: {', '.join(sorted(toolzones.TOOL_ZONES))}")

    keep: list[str] = []
    withheld: dict[str, str] = {}
    for tool in sorted(toolzones.TOOL_ZONES):
        domain = toolzones.TOOL_DOMAINS[tool]
        if tool in disable:
            withheld[tool] = WITHHELD_DISABLED
        elif domains and domain != "*" and domain not in domains:
            withheld[tool] = WITHHELD_DOMAIN
        elif not toolzones.is_available(tool, served):
            withheld[tool] = WITHHELD_ZONES
        else:
            keep.append(tool)
    return {"tools": keep, "withheld": withheld}
