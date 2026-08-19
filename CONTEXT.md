# CONTEXT

Glossary for Exo. Language only — no implementation, no spec.

## The shape of the thing

### exo
Short for **exocortex**: the part of your thinking that lives outside your head.
Exo holds one person's context — what they consumed, what they wrote, what
they concluded — and hands it to whatever assistant is in front of them, without
that assistant's vendor coming to own the record.

Three parts, and the whole system is only ever these three:

| part | what it is |
|---|---|
| **loaders** | what reaches the record — one per source, each writing one zone |
| **the record** | what it holds — tier zones on disk, one SQL surface across them |
| **the surface** | what it exposes — a fixed set of named questions, read-only |

### the record
Everything Exo holds, taken as one place. Previously "the warehouse", which
implied inventory and throughput; what is actually being optimised is recall.
One store, divided by who may write rather than by what the data is.

### the engine / the instance
The **engine** is the code: tiers, the wall, loaders whose input is a *format*,
the publication step, the read surface. It is public and knows the shape of a
life. The **instance** is one person's record, config and plugins — the
contents of a life. It is private, it is a separate repository, and the engine
never learns it exists except through `EXO_HOME` (ADR-0014).

### plugin
What an instance adds that the engine has no business shipping: a loader whose
input is a *place* rather than a format. One city's venues, one person's sibling
repos, one blog's precomputed vectors. The dividing question is never how
specific the code is — it is whether a stranger could hold that input at all.

## Tiers

The record is one place, divided by **who may write**, not by what the data is.

### T0 — consumption
What the outside world recorded about you: scrobbles, ratings, checkins, saves.
Written only by ingest loaders. You do not author T0; you generate it by living.

### T1 — authored
Your words and your records — notes, visits, verdicts, recipes, the item spine.
Written only by you, by hand. Your markdown remains the source of truth; what
sits in T1 is a rebuilt projection of it, never a second original.

Two shapes of authorship live here and they are not interchangeable. The vault is
**draft** — private, unfinished, where a position is still being worked out. The
blog (`t1_post`) is **published** — the same thinking after it was argued into
shape and put on the public internet under your name. A reader that treats them
as one pile will quote a half-formed note when a finished essay exists, or
summarise a public post back to the person who wrote it.

### T2 — derived
What a machine concluded: atoms, vectors, affinities. Regenerable by definition —
losing it costs a rebuild, not a fact.

### the wall
The rule that derivation reads T0 and T1 but never T2. Machine output cannot
become machine input, so T2 can always be rebuilt from ground truth. Not a
convention — the `source` profile physically lacks the T2 views.

### grounds
Whether a row may be believed as evidence about you. Human capture grounds;
machine conclusion does not. Travels with the row, so provenance survives a join.

### stored identity
A row's id is a hash of its content *and its `source` string*, so a source
string is data rather than branding. Renaming one in place re-mints every id
built from it: the ledger reads a life's worth of rows as new, and the surface
announces them as recently added. Source strings written before a rename keep
their old spelling on purpose (ADR-0014 §7).

## Publication

### serve projection
The filtered copy of the record that is allowed to leave this machine — and the
only thing a remote reader ever sees. Not a view or a permission: a physically
separate set of files containing only publishable rows. What is held is *absent*,
not merely unreturned, so no bug or injected instruction downstream can reach it.

### serve / hold
The two decisions available for anything publishable. **Serve** means a remote
reader may see it. **Hold** means it never leaves. There is no third state and no
default — an undeclared thing fails the build rather than guessing, so nothing is
published by being forgotten.

### already public
A blog is the one zone where serve is not a judgement about exposure: every row
is readable on the open internet today, so the projection reveals nothing. That
changes what a good answer looks like rather than what may be published — the
right response is the **link**, not a paraphrase. Every post row carries its live
URL for that reason, built from the instance's own URL template against the
frontmatter slug the site actually resolves permalinks with, never from a filename.

### the two axes
Publication requires **both** an organisational and a semantic yes.

- **folder axis** — where a note is filed. An organisational accident: notes move
  between folders and their content is copied across them.
- **path axis** — where a note sits on the vault's gradient. The semantic claim,
  and the one the vault's glossary makes promises about ("refined/unshared never
  leaves").

Either may veto. A folder decision that happens to coincide with a private
gradient zone is a coincidence, and coincidences are not guarantees.

### content guard
The recognition that holding a *note* does not hold its *words*. The same passage
may exist under a served name — a `-v2`, a draft that absorbed it — so publication
also compares text against everything held, and drops what reproduces it.
Filing is not containment.

### read surface
The remote end of publication: a fixed set of named questions an assistant may
ask of the serve projection, and nothing else. Read-only by construction, not by
permission — there is no write path to be revoked. Its shape is the boundary:
what it cannot ask, it cannot reach.

### the brief
The standing context a reader is handed before it asks anything — who you are,
what you are currently circling, how current each source is, and what can be
asked for. Pushed rather than pulled, because a pull-only surface cannot fix
forgetting that something exists: asking for a thing requires already knowing it
is there.

### staleness
How old the served copy is, stated rather than implied. A reader that cannot see
its own age will present last month's answer as today's.

## The workshop

### the workshop
Your repos, taken as one source (ADR-0011). Not a category of file but a category
of record: the log of what you have been making, which is fresher than your notes
about it because a commit costs no act of capture and a note does.

### project snapshot
The single JSON file `exo scan-projects` writes into `raw/`, from which all four
project zones are built. The indirection is the point: the checkouts exist on one
machine, and a loader that read them directly would build full zones there and
empty ones everywhere else.

### heat
A repo's recency band — active, warm, stalled, dormant. A description of when it
was last touched, never of whether it deserved to be. Dormant includes everything
that simply shipped.

### volume over recency
The rule for ranking repos: commits inside a window, not the date of the last
one. Half of these were `git init`-ed the same week, so last-commit order puts a
one-commit import above a year of work.

### scan-time exclusion
The only privacy lever the workshop has. The folder and path axes describe a
vault and mean nothing to a repo, so a repo that must never leave is kept out by
never being read (`EXO_PROJECTS_DENY`) — not by being filed somewhere.
