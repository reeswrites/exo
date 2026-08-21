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

## Notes

### note source
A place notes come out of — Apple Notes, a Notion export, a directory of text
files, a paragraph on standard input. Not a category of file: a *front door*.
Notes are the one part of the record that keeps changing address, and the shape
that survives that is one where the address is the only thing a new source has
to say (ADR-0017).

### adapter
The code that answers a note source. Five fields — an id, a title, a body, a
date, a folder — and nothing else it may decide. Filenames, frontmatter, identity
and where a note lands are the contract, and the contract has one
implementation, so an adapter cannot get them subtly wrong in its own way.

### the note file
The landed markdown: frontmatter the record reads, body verbatim. It is the
original, not a cache of one. `zones/t1/notes.parquet` is a projection thrown
away on every rebuild; the file is the thing itself, greppable and diffable and
readable by tools that have never heard of Exo.

### landing
The directory a source's notes land in, `notes/raw/<source>/`, one per source.
Its own, because publication matches a note's path zone by longest declared
prefix: a source landing inside an existing zone would inherit that zone's serve
decision on its first run. A landing nothing prefixes fails the build instead,
which is the right answer to a question nobody was asked.

### the mirror
The copy of the record in object storage. One-directional, always: disk is the
record, the bucket receives it, and nothing reads a note back out of a bucket
(ADR-0018). "Keep it in sync" is one phrase doing two jobs — mirroring out is a
backup, mirroring back is a new and unauthenticated author of the authored tier.
Versioning on the bucket is a safety net for the case where the machine is gone;
the restore path that gets tested is git.

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

### publicity
How public a served zone already is, independent of whether it may be served at
all (ADR-0019). `serve` answers *may a reader see this row*; publicity answers
*does a reader seeing all of them cost anything*, and for a blog the answer is
no. Three grades — **published** (a URL you own, intending readers), **profile**
(readable by anyone who visits an account you own; nobody has), **private** —
declared by the instance, absent meaning private.

It sizes the row cap, and more usefully it tells a reader which answers are
quotable: a published row can be linked, a private one is the owner's own
material handed back to them. A zone is as public as its least public row, a tool
as its least public zone.

The caveat it turns on: **already-public is not already-collected.** A public
Letterboxd is public one film at a time; nobody has joined a year of it to a
Goodreads shelf and a commit history. Publicity lowers the blast radius of a row
and not of the join, so grades raise a ceiling rather than remove one.

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

### procedure
How the owner does a recurring thing, written by hand. Every other zone is
descriptive — what was played, rated, written; a procedure is **imperative**, and
it is the one thing here an assistant is meant to act on rather than report.
Published as an MCP *resource* rather than a tool (ADR-0016), because a tool is a
named question whose answer depends on the record and a procedure is a document
whose content does not. That inverts the trust direction, so the only defence
that matters is authorship: hand-written only, never derived, never embedded, and
a malformed one fails the build rather than being projected with a gap in it.

A procedure's serve decision is not its own. It is the AND of its own flag and
the serve status of everything it reads — a served procedure that names a held
zone is a working map of what is being held, published, and the tool returning
nothing does not unsay it.

### the acting rule
Authored text picks the verb and the target; record text may only fill the
payload. A procedure that files something names its sink and its target *in the
procedure*, and nothing a tool returns may move them. Enforced structurally: a
target carrying a placeholder nothing declared fails the build, because that is
the shape where a string injected into a save chooses where an act lands. The
surface stays read-only either way (ADR-0006) — a procedure names the sink, the
agent performs the act.

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
