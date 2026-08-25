"""Which zones each tool on the read surface actually reads, and which it needs.

Data, not logic, and it exists because a procedure's serve decision is not its
own (ADR-0016). A served procedure whose steps say "call `people` for what Sam
cares about" is a working map of what is being held, published — the tool it
names may return nothing, but the procedure states that the thing exists and
that this record holds it. So `exo publish` resolves every tool a served
procedure names into the zones behind it and refuses the build if any of them
is held.

It is independently useful, which is the second reason it is a file rather than
a dict inside publish.py: it is also the answer to "which tools go quiet if I
flip this zone to hold", a question the manifest cannot answer on its own. Since
ADR-0020 that answer is load-bearing rather than merely informative — `publish`
resolves it into the tool list the surface advertises.

Zones listed are the ones whose absence CHANGES THE ANSWER, including the
vector tables behind a semantic tool — `whats_relevant` over a held corpus is
not a narrower answer, it is a different one.

This is NOT the same map as `reads` on the JS side, and the difference is
deliberate. `reads` names the zones whose CONTENT can reach a caller, so it
decides how public an answer may be (ADR-0019) and it excludes the vector
tables, which are machinery a caller never sees. This file names what a tool
DEPENDS ON, so it includes them. A tool can depend on a zone it cannot leak.

## The three buckets (ADR-0020)

Holding a zone does not affect every tool that touches it the same way, and a
single flat list cannot say which. `collection` without `t0_music` still lists
89 records; `collection` without `t1_collection` is nothing at all.

    required   every one must be served, or the tool cannot answer
    any_of     interchangeable corpora; ONE group, fully served, is enough
    enriches   makes the answer better and is never load-bearing

`any_of` holds GROUPS rather than zones because a semantic corpus is a pair: the
content and the index over it. `whats_relevant` survives on the blog alone, but
only if both `t1_post` and `t2_post_vec` are there — a corpus with no index is
unreachable by the only access path the tool has, and an index with no corpus
resolves to rows that are not published.

The map is over TOOL NAMES because that is what an author writes. Nobody writing
a procedure knows that `taste` is backed by `t2_affinity`, and requiring them to
would make the check something people route around.

`tests/test_procedures.py` asserts this covers exactly the tools the worker
defines, so a tool added on the JS side without a line here fails the suite
rather than silently passing every procedure that names it.
"""
from __future__ import annotations

from typing import NamedTuple


class Reads(NamedTuple):
    """What one tool depends on, split by how badly it needs each part."""

    required: tuple[str, ...] = ()
    any_of: tuple[tuple[str, ...], ...] = ()
    enriches: tuple[str, ...] = ()

    def zones(self) -> tuple[str, ...]:
        """Every zone this tool touches, in a stable order, deduped.

        This is what the procedure check reads, and it must stay the UNION.
        Naming a tool in a served procedure advertises everything behind it,
        including the corpus that happens to be optional for availability —
        "you could ask this" is the disclosure, not "this would return rows".
        """
        seen: dict[str, None] = {}
        for z in (*self.required, *(z for g in self.any_of for z in g), *self.enriches):
            seen[z] = None
        return tuple(seen)


# tool name -> what it reads
TOOL_ZONES: dict[str, Reads] = {
    # ── mind ──────────────────────────────────────────────────────────────────
    # Three corpora, each a (content, index) pair. Any one of them answers; the
    # tool only goes quiet when the last one does.
    "whats_relevant": Reads(any_of=(("t2_atom", "t2_atom_vec"),
                                    ("t1_notes", "t2_note_vec"),
                                    ("t1_post", "t2_post_vec"))),
    # Both required: the lookup is semantic (ADR-0007 — there is no id to ask
    # by), so the index is the only way in, and the body is the whole answer.
    "notes_on": Reads(required=("t1_notes", "t2_note_vec")),
    "open_threads": Reads(required=("t1_open_thread",)),
    "posts": Reads(required=("t1_post", "t2_post_vec")),
    "drafts": Reads(required=("t1_draft",)),
    "recent_topics": Reads(required=("t0_chat_topic",)),
    # Three of its four include-modes are turns. A `thread` that can only ever
    # return the title and the distillation is not this tool with less in it.
    "thread": Reads(required=("t0_chat", "t0_chat_topic")),
    # ── culture ───────────────────────────────────────────────────────────────
    "verdicts": Reads(required=("t1_verdicts",)),
    "reviews": Reads(required=("t1_film_review",)),
    # The scrobble stream is the answer; the affinity zone adds one column to it
    # and, being a notes join, would otherwise decide the whole tool's grade
    # (ADR-0023 §2). Held, `taste` still says what he listens to.
    "taste": Reads(required=("t0_music",), enriches=("t2_affinity",)),
    # What he owns is the answer; play counts are colour on it.
    "collection": Reads(required=("t1_collection",), enriches=("t0_music",)),
    # ── table ─────────────────────────────────────────────────────────────────
    "places": Reads(required=("t1_visits",)),
    "recipes": Reads(required=("t1_recipe",)),
    # ── workshop ──────────────────────────────────────────────────────────────
    "projects": Reads(required=("t1_project",), enriches=("t1_project_commit",)),
    "project_activity": Reads(required=("t1_project_commit",)),
    "project_docs": Reads(required=("t1_project_doc",)),
    "project_open": Reads(required=("t1_project_open",)),
    # ── commitments ───────────────────────────────────────────────────────────
    "agenda": Reads(required=("t1_item",)),
    # The append-only log IS the answer here; the spine supplies the names.
    "history": Reads(required=("t1_item_event",), enriches=("t1_item",)),
    # ── world ─────────────────────────────────────────────────────────────────
    "events": Reads(required=("t0_event",)),
    # Somebody else's writing about culture. One zone, no enrichment: an
    # answer here is the press or it is nothing.
    "criticism": Reads(required=("t0_criticism",)),
    "taste_profile": Reads(required=("t1_taste",)),
    # ── cross-domain ──────────────────────────────────────────────────────────
    # Every one of these is parameterised over the surface rather than part of
    # it (ADR-0015 §2), which is exactly why no single zone is load-bearing:
    # holding one medium narrows the answer, it does not remove the question.
    "around_the_time": Reads(any_of=(("t1_notes",), ("t0_music",), ("t0_book",),
                                     ("t0_film",), ("t0_tv",))),
    "backlog": Reads(any_of=(("t0_book",), ("t0_raindrop",))),
    "consumption": Reads(any_of=(("t0_music",), ("t0_book",), ("t0_film",),
                                 ("t0_tv",), ("t0_beer",))),
    "medium": Reads(any_of=(("t0_film",), ("t0_book",), ("t0_tv",), ("t0_music",),
                            ("t0_beer",), ("t1_visits",), ("t1_verdicts",),
                            ("t1_film_review",), ("t1_collection",))),
    "ratings": Reads(any_of=(("t0_film",), ("t0_book",), ("t0_beer",), ("t1_visits",))),
    "saves": Reads(required=("t0_raindrop",)),
    "taste_summary": Reads(required=("t0_taste_derived",)),
}


# tool name -> the `domain` facet it declares on the JS side (ADR-0015 §2).
#
# Mirrored rather than derived, for the same reason TOOL_ZONES is: publish runs
# in Python and must resolve the tool list without a node on the box. Kept
# honest the same way too — `tests/test_procedures.py` reads the facet straight
# out of tools.js and fails on any disagreement.
#
# `"*"` is not a domain, it is a tool parameterised over the surface rather than
# part of it, so it survives every domain filter: narrowing an instance to
# `["mind"]` should not remove `ratings`, which answers about whichever medium
# the caller names.
TOOL_DOMAINS: dict[str, str] = {
    "whats_relevant": "mind",
    "notes_on": "mind",
    "open_threads": "mind",
    "posts": "mind",
    "drafts": "mind",
    "recent_topics": "mind",
    "thread": "mind",
    "verdicts": "culture",
    "reviews": "culture",
    "taste": "culture",
    "collection": "culture",
    "places": "table",
    "recipes": "table",
    "projects": "workshop",
    "project_activity": "workshop",
    "project_docs": "workshop",
    "project_open": "workshop",
    "agenda": "commitments",
    "history": "commitments",
    "events": "world",
    "criticism": "culture",
    "taste_profile": "world",
    "around_the_time": "*",
    "backlog": "*",
    "consumption": "*",
    "medium": "*",
    "ratings": "*",
    "saves": "*",
    "taste_summary": "*",
}


class UnknownTool(KeyError):
    """A tool name nothing on the surface answers to."""


def reads_for(tool: str) -> Reads:
    """The full dependency record for one tool."""
    try:
        return TOOL_ZONES[tool]
    except KeyError:
        raise UnknownTool(tool) from None


def zones_for(tool: str) -> tuple[str, ...]:
    """Every zone behind one tool — the union, for the procedure check.

    Raises rather than returning an empty tuple, and the caller must not soften
    that. A typo in a procedure's `needs:` that resolved to "no zones" would
    pass the held-zone check by naming nothing — which publishes precisely the
    procedure whose dependencies nobody managed to verify.
    """
    return reads_for(tool).zones()


def is_available(tool: str, served: set[str] | frozenset[str]) -> bool:
    """Can this tool answer anything at all, given the zones that were served?

    Fail-closed on a tool that declares no dependencies: that is not a tool
    which needs nothing, it is a line nobody finished writing, and the same
    argument `gradeOf` makes for an empty `reads` applies here.
    """
    r = reads_for(tool)
    if not r.required and not r.any_of:
        return False
    if any(z not in served for z in r.required):
        return False
    if r.any_of and not any(all(z in served for z in group) for group in r.any_of):
        return False
    return True
