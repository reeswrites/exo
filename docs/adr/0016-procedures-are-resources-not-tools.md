# ADR-0016 — A procedure is a resource, not a tool

Status: accepted · 2026-08-19

## Context

The surface holds what the owner consumed, wrote, rated and built. It does not
hold how they *do* things. The weekly digest, the way a hangout gets proposed,
the check before a piece goes out — these are real, repeated, and written down
nowhere the surface can reach, so an assistant asked to run one reconstructs it
from scratch every time and gets a different answer each time.

The obvious place to put them is a tool: `how_i(situation)`. It is the wrong
place, and the reason is the line MCP already draws.

Every tool in `worker/src/tools.js` is a **named question whose answer depends
on the record**. `saves` returns different links this week than last; `agenda`
changes when an item closes. A procedure is the opposite: a document with a
stable identity whose content does not vary with the record at all. That is
`resources` rather than `tools`, and taking the protocol at its word buys
something concrete — `resources/list` is the discovery index, so a new procedure
becomes visible by being written, with no worker deploy and no `tools/list`
cache to invalidate.

It is also a strictly **smaller exposure**. A tool takes arguments, and
arguments are a surface to steer: the shape of ADR-0007's concern is an injected
instruction choosing what to ask for. A resource read takes a URI and nothing
else. For the one zone whose output is imperative, that difference is the whole
budget.

## Decision

**Procedures live in `t1_procedure` and are published as MCP resources at
`exo://procedure/<slug>`, never as a tool.** They are hand-written markdown in
the instance (`$EXO_HOME/procedures/`), projected by `exo/loaders/procedure.py`,
and rendered by the worker rather than returned raw.

Two consequences follow, and they are the substance of this decision.

### 1. This zone inverts the trust direction, so authorship is the whole story

Every other zone returns descriptive rows — this is what was played, this is
what was written. This one returns text an assistant is meant to **act on**. So
"who may write it" stops being bookkeeping and becomes the entire security
story:

- **Hand-authored only.** No loader path into it, no promotion from `captures/`,
  nothing derived. `author="human"` is asserted by the loader, not passed in,
  and nothing else may write `zone="procedure"`.
- **Excluded from embedding.** `t2` atomizes and embeds by naming its source
  zones, and procedures are absent from both passes deliberately. If procedure
  bodies reached `t2_atom`, `whats_relevant` — which promises the owner's prose —
  would start returning imperative text.
- **Validated, fatally.** A missing field fails the build rather than warning. A
  procedure with a gap in it is not a slightly worse procedure; it is a document
  an assistant will follow anyway, filling the gap with its own guess.
- **Capped at 8 KB per serialised row.** The resource path never passes through
  the worker's `cap()`, so this is the only ceiling that exists on what the zone
  hands out. Failing forces decomposition, which is also what makes a procedure
  followable.
- **Rendered, never returned raw.** The worker wraps the body in a frame that
  says whose document this is, states the blocking preconditions above the steps,
  states how old the owner's last verification is, and — for an acting procedure —
  states that the sink and target are fixed by the author. That frame is the only
  thing separating "a document he wrote" from "instructions from this server",
  and it is ten lines.

### 2. A procedure's serve decision is not its own

It is the **AND** of its own `serve:` flag and the serve status of every zone it
reads. A served procedure whose steps say "call `people` for what Sam cares
about" is a working map of what this record holds and what is being kept back —
published. The tool returning nothing does not unsay it.

So `exo publish` resolves each served procedure's `needs.exo` tool names into
zones through `exo/toolzones.py` and **refuses the build** if any of them is
held. Refuses rather than filters, for the reason the whole publication path
exists: a filter is a promise, an absent row is a fact. And a tool name that
resolves to nothing fails too — a typo that silently empties the check is the
failure mode that publishes the thing you meant to hold.

### The acting rule

`kind: action` procedures name a sink and a target. **Authored text picks the
verb and the target; record text may only fill the payload.** The loader enforces
it structurally: a `target` containing a placeholder nothing declared is rejected
at build time, because that is the shape where an injected string inside a save
or a note chooses where an act lands.

The surface itself gains nothing. It is read-only, permanently (ADR-0006); an
acting procedure names a sink and the *agent* performs the act, outside this
system, under whatever confirmation that agent's own rules require.

## Consequences

- Discovery is free and current: writing a file makes a procedure listable. It
  also means every listed procedure is in every client's context by default,
  which is fine at four and wrong at forty — see below.
- `resources/list` is now a query. It is deliberately **not** wrapped in
  try/catch, so the D1 import must land before the worker deploys; a missing
  `t1_procedure` takes the brief down with it, loudly, rather than degrading to
  the brief alone and hiding a broken deploy.
- `exo/toolzones.py` exists as a side effect, and answers a question the manifest
  could not: which tools go quiet if a zone is flipped to hold.
- `kind` stays at two values. Everything else that distinguishes procedures is
  expressible in `needs` and `abort_when`; a growing taxonomy would need
  per-type handling in the loader, in publish, and in the renderer.

## Alternatives rejected

- **A `how_i(situation)` search tool.** The right answer eventually, and only
  once the count outgrows what fits in `resources/list`. It would return URIs
  rather than bodies and hand off to `resources/read` — so it is a discovery
  layer over this decision, not an alternative to it.
- **One tool returning the whole set.** Puts every procedure in every answer and
  reintroduces the argument surface that made a resource the smaller exposure.
- **Deriving procedures from the record** — from repeated chat patterns, from
  what the nightly does. This is the one zone where a machine-written row is
  directly actionable, which makes it the last place derivation belongs.

## What to watch

Both readable from `wh_audit`:

- **Eager vs lazy.** `resources/read` calls clustered at session start with no
  preceding `tools/call` means the client pulls bodies eagerly — every procedure
  in every context window. That is what moves the search tool up the queue.
- **Dead triggers.** A served procedure with zero unprompted reads in a month has
  a `trigger` line that matches no real moment. That is the field to rewrite,
  and the audit log is the only place the failure is visible.
