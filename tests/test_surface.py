"""The modular surface (ADR-0020): which tools an instance offers, and why not.

The point of these is the *asymmetry*. Everything else in this codebase fails
closed because forgetting leaks; this resolves the opposite way because the tool
list cannot widen what leaves and failing closed would hide new tools. A test
that only checked the happy path would let that invert silently.
"""
from __future__ import annotations

import pytest

from exo import surface, toolzones

ALL_ZONES = frozenset(z for t in toolzones.TOOL_ZONES for z in toolzones.zones_for(t))


def _names(served, **kw):
    return set(surface.resolve(served, **kw)["tools"])


# ───────────────────────────── the dependency map ─────────────────────────────


def test_zones_for_is_the_union_of_all_three_buckets():
    """The procedure check reads this and must keep seeing everything.

    Naming a tool in a served procedure advertises the whole thing behind it —
    "you could ask this" is the disclosure. Narrowing this to the required zones
    would let a procedure publish while pointing at a held corpus.
    """
    for tool in toolzones.TOOL_ZONES:
        r = toolzones.reads_for(tool)
        union = set(r.required) | {z for g in r.any_of for z in g} | set(r.enriches)
        assert set(toolzones.zones_for(tool)) == union, tool
        # and no duplicates, since it feeds a membership check downstream
        assert len(toolzones.zones_for(tool)) == len(union), tool


def test_every_tool_declares_at_least_one_dependency():
    """A tool that needs nothing is a line nobody finished writing."""
    for tool in toolzones.TOOL_ZONES:
        r = toolzones.reads_for(tool)
        assert r.required or r.any_of, f"{tool} declares no load-bearing zone"


def test_a_tool_declaring_nothing_is_unavailable_rather_than_universal():
    """Fail-closed where it costs nothing: the same argument `gradeOf` makes for
    an empty `reads`. This is the one direction inside this module that stays
    tight, because an undeclared tool is a bug and not a configuration."""
    assert toolzones.TOOL_ZONES  # guard the monkeypatch below against a typo
    orig = toolzones.TOOL_ZONES.get("_probe")
    toolzones.TOOL_ZONES["_probe"] = toolzones.Reads()
    try:
        assert not toolzones.is_available("_probe", ALL_ZONES)
    finally:
        toolzones.TOOL_ZONES.pop("_probe", None)
        if orig is not None:
            toolzones.TOOL_ZONES["_probe"] = orig


def test_unknown_tool_raises_rather_than_resolving_to_nothing():
    with pytest.raises(toolzones.UnknownTool):
        toolzones.zones_for("no_such_tool")


# ─────────────────────────────── availability ────────────────────────────────


def test_everything_served_offers_every_tool():
    assert _names(ALL_ZONES, disable=[], domains=[]) == set(toolzones.TOOL_ZONES)


def test_required_zones_retire_their_tool():
    served = ALL_ZONES - {"t1_recipe"}
    offered = surface.resolve(served, disable=[], domains=[])
    assert "recipes" not in offered["tools"]
    assert offered["withheld"]["recipes"] == surface.WITHHELD_ZONES


def test_an_enriching_zone_never_retires_its_tool():
    """`collection` without play counts still lists 89 records."""
    assert "collection" in _names(ALL_ZONES - {"t0_music"}, disable=[], domains=[])
    assert "projects" in _names(ALL_ZONES - {"t1_project_commit"}, disable=[], domains=[])
    assert "history" in _names(ALL_ZONES - {"t1_item"}, disable=[], domains=[])


def test_any_of_survives_while_one_group_is_intact():
    """The decision this file exists for: holding the notes must not silently
    retire the two tools that answer perfectly well without them."""
    served = ALL_ZONES - {"t1_notes", "t2_note_vec", "t2_atom", "t2_atom_vec"}
    offered = surface.resolve(served, disable=[], domains=[])
    assert "whats_relevant" in offered["tools"]   # survives on the blog
    assert "around_the_time" in offered["tools"]  # survives on consumption
    assert offered["withheld"] == {"notes_on": surface.WITHHELD_ZONES}


def test_any_of_needs_a_WHOLE_group_not_a_zone_from_each():
    """A corpus with no index is unreachable by the only access path the tool
    has, so half of every pair is not half an answer — it is none."""
    half = {"t2_atom", "t1_notes", "t1_post"}  # content, no vector tables
    assert not toolzones.is_available("whats_relevant", half)
    assert toolzones.is_available("whats_relevant", half | {"t2_post_vec"})


def test_the_last_corpus_going_dark_does_retire_the_tool():
    served = ALL_ZONES - {"t1_notes", "t2_note_vec", "t2_atom", "t2_atom_vec",
                          "t1_post", "t2_post_vec"}
    assert "whats_relevant" not in _names(served, disable=[], domains=[])


# ────────────────────────────── the config levers ─────────────────────────────


def test_disable_switches_a_tool_off_with_its_zones_intact():
    offered = surface.resolve(ALL_ZONES, disable=["notes_on"], domains=[])
    assert "notes_on" not in offered["tools"]
    assert offered["withheld"]["notes_on"] == surface.WITHHELD_DISABLED


def test_a_typo_in_the_deny_list_is_loud():
    """Silently offering the tool somebody decided against is the one outcome a
    deny-list must never produce."""
    with pytest.raises(surface.UnknownToolInConfig):
        surface.resolve(ALL_ZONES, disable=["notes-on"], domains=[])


def test_domains_narrow_the_instance():
    offered = surface.resolve(ALL_ZONES, disable=[], domains=["mind"])
    assert "notes_on" in offered["tools"]      # mind
    assert "recipes" not in offered["tools"]   # table
    assert offered["withheld"]["recipes"] == surface.WITHHELD_DOMAIN


def test_star_domain_tools_survive_every_domain_filter():
    """`"*"` is not a domain — it is a tool parameterised over the surface
    (ADR-0015 §2). Narrowing to `mind` should not remove `ratings`."""
    offered = surface.resolve(ALL_ZONES, disable=[], domains=["mind"])
    for tool, domain in toolzones.TOOL_DOMAINS.items():
        if domain == "*":
            assert tool in offered["tools"], tool


def test_no_domains_configured_means_no_domain_filtering():
    assert _names(ALL_ZONES, disable=[], domains=[]) == set(toolzones.TOOL_ZONES)


def test_disable_is_reported_ahead_of_a_held_zone():
    """Precedence is for the reader: "you turned it off" is more useful than
    "and also its zones are held"."""
    served = ALL_ZONES - {"t1_recipe"}
    offered = surface.resolve(served, disable=["recipes"], domains=[])
    assert offered["withheld"]["recipes"] == surface.WITHHELD_DISABLED


def test_every_tool_is_accounted_for_exactly_once():
    offered = surface.resolve(ALL_ZONES - {"t1_recipe"}, disable=["saves"], domains=["table"])
    assert set(offered["tools"]).isdisjoint(offered["withheld"])
    assert set(offered["tools"]) | set(offered["withheld"]) == set(toolzones.TOOL_ZONES)
