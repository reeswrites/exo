# ADR-0027 — A skill ships with the engine; a procedure belongs to the owner

Status: accepted · 2026-09-02

## Context

ADR-0016 settled where imperative text lives, for one direction of it. A
procedure is *how this person does a recurring thing*, so it is hand-written in
the instance, it is published as a resource rather than a tool, and the whole
security story is that nothing but the owner may write it.

The other direction was never named, and it shows up the moment any assistant is
pointed at the surface. It calls `whats_relevant` and quotes a `derived` row as
the owner's own sentence. It reads `saves` as things they read, when a save is
`intent` and nobody prunes those. It prints a rollup from `ratings` without
calling `taste_summary` first, and reports an 8 as praise on a scale that runs
high. It asks `thread` for turns and hands back the assistant's half.

None of that is a bug in the record. Every one of them is a false statement
assembled out of true rows, and ADR-0015 already predicted the whole set: `class`
is precisely the facet that says which lie a tool affords, and `kind` is the one
that predicts how a row goes wrong. The knowledge that prevents them is not
knowledge about a life. It is knowledge about *this surface* — the same 32 named
questions, with the same declared facets, in every instance that runs the engine.

It currently lives nowhere. Each client rediscovers it per session, from scratch,
and gets a different amount of it right each time.

A tool description carries one sentence of this, and that is where the sentence
belongs. What a description cannot carry is an order of operations across eight
tools, or *date every artifact before you write a word*, or what to say when the
honest answer is that the record holds nothing.

## Decision

**`skills/` in the engine. A skill is markdown instructions for an assistant
reading an Exo surface — shipped with the code, reviewed like code, identical in
every instance.**

The line between a skill and a procedure is the line ADR-0014 already drew for
loaders, one level up. **Format or place.**

- A **procedure's** input is a *place*: your Sunday evening, your Todoist
  project, the check you run before a piece goes out. It belongs to the
  instance, it is hand-written there, and it reaches a client as a resource at
  `exo://procedure/<slug>`.
- A **skill's** input is a *format*: the read surface itself. Named tools with
  declared facets and a read-only guarantee — a thing any stranger running the
  engine holds. It belongs here, and it reaches a client by being copied there.

Both are text an assistant acts on, which is why an engine skill is bounded by
five rules rather than by taste.

**1. The engine surface only.** No peer, no second server, no client stack. An
instance chooses what else it connects (ADR-0020); a skill that names one has
that instance's setup compiled into it and is an instance skill in engine
clothes. Where a peer would help, a skill may say so generically — a peered row
already returns the peer's own id — and stop there.

**2. Every tool it needs is named, and absence is handled.** An instance offers a
subset: `[tools]` narrows by domain or by name, and a tool whose zones are held
retires itself. A skill written against all 32 breaks on the first instance that
kept two domains. `Needs` is a section of the file, and each entry says what the
skill still does without that tool.

**3. No claim about the owner.** Not their prose style, not their volume, not
their habits. The engine cannot know any of it. Where a skill has to tell the
owner's words from a machine's, it reads the `class` facet, the speaker tags and
the notes the tools already return — `summary_is`, `dialogue_note`, the class
line itself. Those are guarantees. A heuristic about how somebody types is a
guess that fails silently on everyone else.

**4. No counts.** A row count is an instance fact, and it decays in a public
repository whether or not anyone notices.

**5. It reads and reports; it does not act.** The surface is read-only,
permanently (ADR-0006). Anything naming a sink and a target is `kind: action` —
a procedure, in the instance, under the acting rule.

Rules 1 and 3 are the ones that do the work. Together they say: a skill may
encode how to *read* this surface, and nothing about who is behind it.

## Consequences

- **The leak guard grows a tree.** `tests/test_no_personal_strings.py` covered
  `exo/` and `worker/src`, and no markdown at all. Skills are imperative text in
  the public repo — the one place a stray instance detail would be *followed*
  rather than merely read — so `skills/` and `.md` join the guard.

- **Rule 4 arrives as a debt, not a clean sheet.** `worker/src/tools.js` already
  spends row counts and one instance's collection names inside tool
  descriptions, and those strings reach every reader of the public repo. The
  skills do not add to it. Writing the rule down is what makes the existing
  lines visible as debt rather than as prose.

- **Discovery is not free here, and it is free for a procedure.** Writing a
  procedure file makes it listable with no deploy; a skill has to be copied into
  a client. That is the cost of not being per-instance, and it is the right way
  round: the per-instance thing gets the per-instance delivery.

- **A skill and a procedure can hold the same name.** The instance copy is the
  more specific one and should win. Install one.

## Alternatives rejected

- **Ship skills as procedures.** One mechanism instead of two, and wrong twice.
  It writes engine text into `t1_procedure`, whose entire security story is that
  nothing but the owner writes there; and it makes a per-instance publish the
  delivery path for something that does not vary by instance.

- **A tool that returns the right skill.** The objection ADR-0016 raised against
  `how_i(situation)` applies harder here: an argument is a surface to steer, and
  this is the imperative kind of text (ADR-0007). A file copied into a client
  takes no argument at all.

- **Leave it in the tool descriptions.** They carry the one-sentence version
  already. Sequence, dating and what-to-say-when-empty do not fit in a
  description, and a description that grew to hold them would be read on every
  call for the benefit of one.

- **No skills; let each client work it out.** The status quo. What it costs is
  `derived` rows quoted as the owner's prose, which is the failure ADR-0015
  named and then had nowhere to put the fix.

## What to watch

- **A skill with an `if your instance has X` branch is two skills.** The branch
  is where a place gets in.
- **A `Needs` list naming most of the surface is a skill with no thesis.** Each
  one here exists to prevent a specific false statement; if it cannot name that
  statement, it is a tour of the tools.
- **The first pull request adding a skill that names a second MCP server.** That
  is the test of rule 1, and the answer is that it belongs in the contributor's
  instance.
