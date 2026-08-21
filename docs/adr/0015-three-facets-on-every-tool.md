# ADR-0015 — Three facets on every tool, and the empty cells are the roadmap

Status: accepted · 2026-08-19

## Context

ADR-0013 said what the read surface *is* — a data layer that answers what is
true. It did not say what shape the surface has. Twenty-eight tools arrived one
at a time, each justified on its own, and the only structure over them was the
order they were written in, which records nothing except which gap was noticed
first.

The obvious grouping is by subject: film tools, note tools, repo tools. It falls
apart immediately. `ratings` covers films, books, beer and restaurants.
`medium` takes the subject as an argument. `around_the_time` spans everything
there is. A third of the surface refuses to sit in a subject bucket, and the
tools that refuse are the most-used ones.

The grouping that *does* hold is the one already load-bearing on the write side.
Zones are separated by who may write, not by what the data is. The read side has
the same seam and had never named it: what a caller most needs to know about a
row is not its subject but the act that produced it. `taste` and `taste_profile`
return overlapping subject matter and mean opposite things — one is play counts,
one is what the owner says they like — and ADR-0013 already leaned on that
distinction in prose without anything in the code carrying it.

## Decision

**Every tool declares three orthogonal facets: `class`, `domain`, `kind`. The
vocabularies are closed, and `test/run.mjs` fails on an unknown value or a
missing one.**

*Amended 2026-08-21 (ADR-0019).* A tool now also declares `reads` — the zones
whose content can reach a caller — and it is enforced in the same place, for a
sharper reason. The three facets describe an answer; `reads` decides how public
one is allowed to be, so drift there can only ever be too permissive. It is not
a fourth facet: a facet is a closed vocabulary somebody chooses from, and this is
a claim about the SQL that the tests check against the SQL.

**1. `class` is the act that produced the row — the facet that decides whether
an answer is a lie.** Eight values: `revealed` (they did it, no intent stated),
`authored` (their words, deliberately), `intent` (declared want, not
consummated), `possession` (paid for and kept), `dialogue` (co-authored, half of
it is another model), `derived` (a machine concluded it), `world` (not about
them at all), `lens` (owns no rows; joins the others).

This is the read-side mirror of write-scope. `revealed` and `world` come from
T0, `authored`/`intent`/`possession` from T1, `derived` from T2 — and `lens` is
the only class that breaks the mirror, which is exactly what makes it worth
naming separately. A caller that quotes a `derived` row as the owner's own words,
or reads an `intent` row as something that happened, has produced a false
statement from true data. Nothing else on the tool def prevented that.

**2. `domain` is what the rows are about, and `"*"` means the caller chooses.**
Six domains — `culture`, `table`, `mind`, `workshop`, `commitments`, `world` —
plus `"*"` for tools that take the domain as an argument rather than being fixed
to one. Seven of twenty-eight are `"*"`, and that is a real species distinction
rather than a filing failure: `medium`, `ratings`, `consumption`, `backlog`,
`saves`, `taste_summary` and `around_the_time` are parameterised over the
surface rather than being part of it.

`class: "lens"` implies `domain: "*"` and the test asserts it. A lens that
acquired a fixed domain stopped being a lens and became a tool that should say
which zone it reads.

Five of the six domains already have a counterpart vertical in taste-engine
(media, eating, muse, kairos, events). The names should converge on one set;
`workshop` is the one with no counterpart.

**3. `kind` is the shape of one row, and it predicts the failure mode.** Seven
values: `event`, `judgement`, `text`, `entity`, `pointer`, `vector`, `mixed`. A
`judgement` is unreadable without its scale — this is the dining-median-8.1
problem, and it is a property of the row shape rather than of that one tool. A
`pointer` goes stale silently because nobody prunes them, which `project_open`
already warns about in prose and which is true of `saves`, `backlog`,
`open_threads` and `agenda` too. A `vector` is never quotable. An `event` is the
only kind that cannot lie about recency.

**4. Two tools are reclassified by taking the facets seriously.**
`open_threads` reads as authored — they are his sentences — but an unclosed
question is a pointer to unfinished thinking, not a statement of it, so it files
under `intent` beside `agenda` and `backlog`. `projects` looked like a lens
because it aggregates; it returns repo entities he owns, so it is `possession`,
and that leaves the `lens` class holding only `medium` and `around_the_time`,
which is the honest size of it.

**5. The facets are internal. `tools/list` still carries name, description and
inputSchema, and a test enforces it.** The MCP descriptor is a contract with
every client; our filing system is not part of it. What the facets earn their
keep on is our side: the README table is generated from them and grouped by
domain, so the surface is legible from outside without a hand-maintained doc
going stale (the failure ADR-0013 already caught twice).

## Consequences

Crossing `domain` with `class` produces a grid, and the empty cells are the
roadmap — this is the part worth having done the work for. Ranked by what they
would buy:

1. **world × revealed — there is no attendance log.** `events` says what is
   available; nothing records what he went to. `culture` gets its sharpest
   reading from revealed-versus-stated (`taste` against `taste_profile`), and
   `world` has only the stated half, so every event recommendation the personal
   OS makes is currently unfalsifiable.
2. **mind × revealed** — no reading behaviour: what he opened, highlighted,
   returned to. `saves` is intent, not consumption. `mind` is the one domain
   judged entirely on what he says about his own thinking.
3. **commitments × derived** — streaks and completion rates are computable from
   `history` today and nothing exposes them. Cheapest of the five.
4. **table × possession** — `culture` has `collection`, on the argument that
   buying and keeping a thing is a stronger signal than consuming it once. The
   kitchen and cellar equivalent does not exist.
5. **workshop × derived** — no affinity or clustering over the repos, so nothing
   answers what forty repos are about taken together.

None of these is a tool to add today. They are the standing account of what the
surface cannot answer, which is the thing a flat list of twenty-eight tools was
structurally incapable of telling anyone.

Adding a tool now costs three decisions before it can ship. That is deliberate,
and it is the same trade as ADR-0007: a tool is an exposure decision, and being
made to say what class of evidence it exposes is the cheapest possible version
of that.

What this does NOT settle: whether the facets should reach clients — the brief
is a plausible home for the class vocabulary, since ADR-0013 makes an honest
account of our own shape a standing duty, and a caller that knew `derived` from
`authored` would misquote less. That is a change to what we tell agents and
deserves its own hearing. Also unsettled: whether `workshop` should acquire a
taste-engine vertical, or whether the repos are a source that no recommender
should ever rank.
