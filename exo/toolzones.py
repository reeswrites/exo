"""Which zones each tool on the read surface actually reads.

Data, not logic, and it exists because a procedure's serve decision is not its
own (ADR-0016). A served procedure whose steps say "call `people` for what Sam
cares about" is a working map of what is being held, published — the tool it
names may return nothing, but the procedure states that the thing exists and
that this record holds it. So `exo publish` resolves every tool a served
procedure names into the zones behind it and refuses the build if any of them
is held.

It is independently useful, which is the second reason it is a file rather than
a dict inside publish.py: it is also the answer to "which tools go quiet if I
flip this zone to hold", a question the manifest cannot answer on its own.

The map is over TOOL NAMES because that is what an author writes. Nobody writing
a procedure knows that `taste` is backed by `t2_affinity`, and requiring them to
would make the check something people route around.

Zones listed are the ones whose absence CHANGES THE ANSWER, including the
vector tables behind a semantic tool — `whats_relevant` over a held corpus is
not a narrower answer, it is a different one.

`tests/test_procedures.py` asserts this covers exactly the tools the worker
defines, so a tool added on the JS side without a line here fails the suite
rather than silently passing every procedure that names it.
"""
from __future__ import annotations

# tool name -> the zones it reads
TOOL_ZONES: dict[str, tuple[str, ...]] = {
    # mind
    "whats_relevant": ("t2_atom", "t2_atom_vec", "t2_note_vec", "t2_post_vec",
                       "t1_notes", "t1_post"),
    "notes_on": ("t1_notes", "t2_note_vec"),
    "open_threads": ("t1_open_thread",),
    "posts": ("t1_post", "t2_post_vec"),
    "drafts": ("t1_draft",),
    "recent_topics": ("t0_chat_topic",),
    "thread": ("t0_chat", "t0_chat_topic"),
    # culture
    "verdicts": ("t1_verdicts",),
    "reviews": ("t1_film_review",),
    "taste": ("t2_affinity",),
    "collection": ("t1_collection", "t0_music"),
    # table
    "places": ("t1_visits",),
    "recipes": ("t1_recipe",),
    # workshop
    "projects": ("t1_project", "t1_project_commit"),
    "project_activity": ("t1_project_commit",),
    "project_docs": ("t1_project_doc",),
    "project_open": ("t1_project_open",),
    # commitments
    "agenda": ("t1_item",),
    "history": ("t1_item", "t1_item_event"),
    # world
    "events": ("t0_event",),
    "taste_profile": ("t1_taste",),
    # cross-domain
    "around_the_time": ("t1_notes", "t0_music", "t0_book", "t0_film", "t0_tv"),
    "backlog": ("t0_book", "t0_raindrop"),
    "consumption": ("t0_music", "t0_book", "t0_film", "t0_tv", "t0_beer"),
    "medium": ("t0_film", "t0_book", "t0_tv", "t0_music", "t0_beer",
               "t1_visits", "t1_verdicts", "t1_film_review", "t1_collection"),
    "ratings": ("t0_film", "t0_book", "t0_beer", "t1_visits"),
    "saves": ("t0_raindrop",),
    "taste_summary": ("t0_taste_derived",),
}


class UnknownTool(KeyError):
    """A tool name nothing on the surface answers to."""


def zones_for(tool: str) -> tuple[str, ...]:
    """The zones behind one tool.

    Raises rather than returning an empty tuple, and the caller must not soften
    that. A typo in a procedure's `needs:` that resolved to "no zones" would
    pass the held-zone check by naming nothing — which publishes precisely the
    procedure whose dependencies nobody managed to verify.
    """
    try:
        return TOOL_ZONES[tool]
    except KeyError:
        raise UnknownTool(tool) from None
