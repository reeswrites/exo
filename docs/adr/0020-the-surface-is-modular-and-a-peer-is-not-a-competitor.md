# ADR-0020 — The surface is modular, and a peer is not a competitor

Status: accepted · 2026-08-23

## Context

ADR-0013 settled that this is a data layer and not the front door for the whole
personal OS. It observed, in passing, that there were *already two MCP surfaces*
— this one and taste-engine's stdio server — and then said nothing about what
either owes the other. That was fine while the second surface answered a
disjoint question. It stops being fine the moment a peer answers the **same**
one.

An assistant connected to this record and to the workspace the notes were
ingested from has two ways to read one note. Nothing anywhere says which to
prefer, that they are the same note, or that a caller reading both has read one
paragraph twice. Two tools answering one question is not free redundancy; it is
a caller choosing with no basis.

Underneath that sat a smaller failure nobody had named. The engine ships one
tool table and every instance gets all 28 of it. `toolzones.py` has known which
zones each tool needs since ADR-0016, and its own docstring advertised the use:

> it is also the answer to "which tools go quiet if I flip this zone to hold", a
> question the manifest cannot answer on its own.

Nothing called it for that. `tools/list` mapped over `TOOLS` unconditionally, so
flipping `t1_recipe` to hold left `recipes` advertised, and calling it raised a
D1 *no such table* that `index.js` reported as `tool failed`. A configuration
choice reached the caller as a malfunction.

## Decision

**An instance offers a subset of the engine's tools, resolved at publish time,
and it declares which of its sources a peer also serves.**

### 1. Dependencies are graded, because holding a zone does not affect every
tool that touches it the same way

A flat list of zones per tool cannot express the difference between `collection`
without `t0_music` — which still lists 89 records — and `collection` without
`t1_collection`, which is nothing at all. So `TOOL_ZONES` carries three buckets:

    required   every one must be served, or the tool cannot answer
    any_of     interchangeable corpora; ONE group, fully served, is enough
    enriches   makes the answer better and is never load-bearing

`any_of` holds **groups** rather than zones, and that is load-bearing rather
than tidy. A semantic corpus is a pair — the content and the index over it — and
`whats_relevant` survives on the blog alone only if `t1_post` *and* `t2_post_vec`
are both there. A corpus with no index is unreachable by the only access path
the tool has (ADR-0007: there is no id to ask by), and an index with no corpus
resolves to rows that were not published. Half a pair is not half an answer.

The payoff is exact where it matters most. Holding the notes retires `notes_on`
and nothing else: `whats_relevant` keeps answering over the blog, and
`around_the_time` over consumption. A single flat list would have retired all
three, and an all-or-nothing rule in the other direction would have kept
`notes_on` advertised over a corpus that is not there.

`zones_for()` still returns the **union**, because the procedure check (ADR-0016)
must keep seeing everything. Naming a tool in a served procedure advertises the
whole thing behind it — *"you could ask this"* is the disclosure, not *"this
would return rows"*.

### 2. `exo.toml` gets two levers, and neither is about privacy

    [tools]
    domains = ["mind", "workshop"]   # specialise this instance
    disable = ["recipes"]            # a peer answers this better

`domains` reads the facet ADR-0015 already closed. `"*"` survives every filter,
because it is not a domain — it is a tool parameterised over the surface rather
than part of it, and narrowing an instance to `mind` should not remove `ratings`.

A name in `disable` that no tool answers to is an **error**. Silently offering
the tool somebody decided against is the one outcome a deny-list must not
produce.

### 3. Publish resolves it; the surface reads a list

Same rule the exposure axis follows, for the same reason: a default chosen in
two places is a default that will eventually differ, and the worker cannot see
`exo.toml` at all. `publish` writes `surface.json` into the bundle and the worker
filters against it. Resolution runs against **what actually reached the
projection**, not against the manifest — a zone can be marked serve and still be
absent because nothing has written it yet, and a tool over an absent table fails
the same way whichever reason it is missing for.

`tools/call` on a tool this instance does not offer says so distinctly from
`unknown tool`. A caller working from a stale list should learn that the tool is
real and this record does not answer with it, rather than that it hallucinated
the name.

### 4. It fails OPEN — the opposite of everything around it

Every other gate here fails closed, because forgetting one leaks. This one does
not, and the asymmetry is the decision rather than an oversight:

- **It cannot widen what leaves.** A held zone is *absent from the projection*,
  so a tool reaching for it finds nothing whether or not it was advertised. The
  tool list is an ergonomic claim about what is worth calling. The D1 import is
  what decides that anything exists.
- **Failing closed would hide every new tool.** An engine that shipped one would
  make it invisible until each instance named it — precisely the "publishing is
  not offering" failure ADR-0013 called a *standing duty*, after `t1_item` sat in
  production for weeks with nothing reading it.

So a missing, malformed or unreachable `surface.json` means every tool the engine
defines. `[]` is distinct from that: an instance deliberately offering nothing.

### 5. A peer is declared, and Exo states the fact rather than acting on it

    [peers]
    sources = { notion = "Notion" }

Keyed on the note `source`, which is already stamped per row by whichever
adapter landed it (ADR-0017). To make the claim usable, `t1_notes` now also
carries the frontmatter `uuid` — the id the note has in the system it came out
of. `source` alone says a peer holds this note; it cannot say *which row*, and
without a join key an assistant holding both surfaces cannot tell one note from
two.

`notes_on(full: true)` returns both, plus a sentence naming the peer: *this copy
is the filed and indexed one, that copy is the current one.* That is where it
stops. Whether to re-read the live page, cite it, or ignore the duplication is
the agent's call — it knows what is already in its context and we do not
(ADR-0013 §2). The brief carries the same fact standing, in the guarded tail,
where a clip cannot lose it.

**The comparative advantage is the reason to state this rather than to withdraw.**
A workspace holds the current text. What it does not hold is the filing, the
publication decision, the atoms, the vectors, or a grade saying how public any
of it is. Exo is the *defined* copy. Deferring the body would trade the half we
are better at for the half we are not.

## Consequences

Adding `uuid` to the notes payload re-mints `t1_notes.id`, and therefore
`t2_note_vec`, on the next rebuild. Both are regenerable by construction, the
embed cache is content-addressed so nothing is re-embedded, and `first_seen` is
stamped on T0 only — so no recency signal resets. Atom ids hash `(span, ref)`
and do not move at all.

`TOOL_DOMAINS` is a Python mirror of a JS fact, carried for one reason: publish
resolves the tool list and cannot run node to do it. `tests/test_procedures.py`
reads the facet straight out of `tools.js` and fails on any disagreement, the
same way the tool-name map has been kept honest since ADR-0016.

What this does **not** settle: whether a peer should be able to contribute a
tool rather than only shadow one. A plugin can already create a zone
(`plugins.py`) and still cannot expose a tool over it, because tools are JS in a
Worker and plugins are Python on the laptop. The declarative-tool-spec shape
that would close it — name, facets, reads, a SELECT-only template — is real
work and belongs to its own decision, not to this one.
