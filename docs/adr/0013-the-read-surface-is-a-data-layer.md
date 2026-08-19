# ADR-0013 — The read surface is a data layer, and Poke is the agent

Status: accepted · 2026-08-19

## Context

The surface grew for weeks without anyone saying what it was. Zones landed,
tools were added when something was obviously missing, and each addition was
judged on its own. That worked until the question arrived in general form: the
warehouse holds dc-events and taste-engine as zones and does not hold
friend-radar, the Kairos scheduler, or muse — should the MCP become the front
door for the whole personal OS?

Checking first showed the question was half wrong. The Kairos item spine WAS
published — `t1_item` and `t1_item_event`, in production D1 the whole time —
with no tool reading it. `buildPlan` was not in some unreachable repo either; it
sits in `taste-engine/src/core/plan.ts` beside `slot`, `match` and `rank-pile`,
and taste-engine already ships its own MCP server over stdio. So there were
already two MCP surfaces, and the reachable one was missing tools rather than
missing data.

That left the real question. taste-engine's stdio server exposes seven tools:
five read, and two — recording a reaction, persisting an elicited preference —
that write, and those two are the flywheel rather than an accessory.

## Decision

**The remote read surface is a data layer. It answers what is true. It does not
decide what to do about it.**

**1. It ranks by relevance to a query. It never ranks by preference.** Retrieval
is finding: `whats_relevant`, `posts` and `notes_on` sort by semantic similarity
because a caller cannot run cosine over 9,708 vectors itself. Judgment — *this
event suits the owner better than that one* — is not ours. Every ORDER BY in the
tool surface is over a measured fact (created, plays, rating, turns, start,
count), and that is the invariant, not a coincidence. `event_pitches` sits on
the far side of the line and stays in taste-engine.

**2. Conveyance to the human belongs to the agent. Conveyance to the agent
belongs to us.** Poke decides whether to link, quote, summarise or stay quiet; it
knows whether the owner is at a desk or walking, and we do not. What we owe it
is an honest account of our own shape, which it cannot recover from the rows:
calibration (a dining 8 is average, because the median is 8.1), coverage gaps
(no watchlist was ever exported, so absent films are a hole in the data and not
an empty queue), provenance, recency, and — through the brief — what exists at
all.

That second obligation is why "publishing is not offering" kept recurring.
Ratings reached D1 with no tool. `t1_item` sat in production unreachable.
`backlog` and `around_the_time` shipped and were never advertised. Each was
read as a mistake; it was a layer nobody owned. Naming it makes it a standing
duty, and `worker/test/run.mjs` now fails when the brief stops naming a
capability.

**3. Scoring is shared; conveyance is not.** A scoring core is data-in,
ranking-out and should have one implementation both hosts import — which is why
`buildPlan` taking its own inputs (`loadConfig`, `initCalendar`,
`registerVerticals`) is the coupling worth removing, and why `slot.ts` and
`match.ts`, already pure, are the shape to move toward. A pitch, by contrast, is
a sentence to Poke, a card in Life Terminal and a table in the CLI: same
ranking, three conveyances, and folding that into a shared core would be as
wrong as duplicating the scoring.

**4. Feedback is not ours, and this resolves rather than defers it.** Reactions
are an input to ranking. If ranking is not ours, its inputs are not either. The
flywheel spins where the engines live. ADR-0006 stands unamended — and an
append-only feedback channel, which had looked like a reasonable compromise,
turns out to be unnecessary rather than merely unbuilt.

## Consequences

The near-term work collapses into exposing rows already published: `agenda` and
`history` over the item spine, `recipes` over `t1_recipe`. No ranking, no
pitching, no writes.

`medium` is the shape this licenses. Answering "what are they like about film"
took four calls to four tools that a caller had to know existed; one call now
returns consumption, ratings on that medium's own scale, what is owned and what
has been written. It is a join, not a judgment, and it is squarely data-layer.

`t2_affinity.score` — `plays + mentions * 500` — is dropped from the projection.
It is the one preference weight that ever reached served data, nothing read it,
and that arbitrary 500 is exactly what this ADR says the surface does not do.
The column stays in the store, where it remains a worked cross-zone example.

The brief no longer tells a reader to link rather than summarise, which retires
the earlier rule that a published post is always best answered with its link.
Carrying the URL on every row is a fact about the data. What to do with it is
the reader's call.

What this does NOT settle: whether friend-radar's inputs should be ingested. It
is the owner's data and it fits the layer, but publishing people's names is a
policy decision that deserves its own hearing rather than arriving as
gap-filling.
