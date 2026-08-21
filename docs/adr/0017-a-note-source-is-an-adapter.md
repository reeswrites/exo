# ADR-0017 — A note source is an adapter, and the note file is the contract

Status: accepted · 2026-08-20

## Context

Exo had one note ingester and it was Apple Notes. `ingest_notes.py` read a local
`NoteStore.sqlite` through the vendored decoder, rendered each note to markdown,
and wrote it into `notes/raw/import/`. One file, four jobs, three of which had
nothing to do with Apple:

1. reach the source
2. normalise what it gives you
3. write an Exo-owned file, idempotently
4. get that file into the record

Steps 2–4 were already generic in everything but name. Step 1 is the only part
that knows what a `ZICCLOUDSYNCINGOBJECT` row looks like.

That mattered the moment the owner's notes stopped living in Apple Notes.
Moving to Notion — or to a folder of markdown, or to whatever comes after Notion
— meant either writing a second three-quarters-identical ingester or losing the
note record, which is the tier the whole read surface is built on: `t1_notes`
feeds `t2_atom`, both vector tables, the search tool and half the brief.

There was a second, quieter gap. `t1_index.notes()` only ever read
`config.VAULT` — the mirrored authoring vault. The Exo-owned tree the ingester
wrote to was indexed by nothing. The cutover the old docstring described ("flip
the note source with `WH_SECOND_BRAIN_DATA=<exo>/notes`") was an all-or-nothing
switch: until you threw it, an imported note reached no zone; after you threw
it, the vault stopped being read. There was no state in which both existed,
which is the only state a migration actually passes through.

## Decision

**A note source is an adapter: something that yields `SourceNote`s.** That is the
whole interface — an id, a title, a body, a date, a folder. Apple Notes is one.
A Notion export is one. A directory of text files is one. Standard input is one.
An instance's own wiki is one, declared in `plugins/` under `NOTE_SOURCES`.

**The landed markdown file is the contract**, not a row and not a queue message:

```
---
type: raw
created: 2026-02-03
imported: 2026-08-20
source: notion
uuid: <the source's own id>
folder: Reading
title: On caching
---
```

Files, because the file is what survives this system. You can grep it, diff it,
put it under git, hand it to a different tool in ten years, and read it back
without Exo existing. `zones/t1/notes.parquet` is a projection of these and is
thrown away on every rebuild; these are the thing itself.

**Identity is `(source, external_id)`**, matched by reading the `uuid:` line back
out of what has already landed. Same note unchanged → skipped. Edited →
overwritten in place. Retitled → keeps its file. Nothing is ever deleted: a note
withdrawn upstream stays landed, because a silo losing your writing is not a
decision you made.

**Every source lands in its own directory, `notes/raw/<landing>/`.** Publication
matches a note's path zone by longest declared prefix (`publish._zone_of`), so a
source that landed *inside* an existing zone would inherit that zone's decision —
`raw/import` serves, so `raw/import/notion/` would have served, silently, on the
first run of a new adapter. Its own top-level directory matches nothing instead,
and the next publish stops with *"note(s) under no declared path_zone"*. That
refusal is the design: a new source is a new question about what leaves this
machine, and it is not answered by a decision made about a different one.

**`t1_index` reads both trees.** The vault keeps `source = "second-brain"` —
stored identity, and every atom on disk hashes the `origin_ref` beside it. The
Exo-owned tree takes each row's source from the file's own frontmatter, so a note
says which silo it came out of rather than being labelled with the name of the
directory it happens to sit in. When an instance finishes the cutover it points
`EXO_VAULT` at the notes tree and the two collapse into one with nothing else to
change.

### What ships in the engine

`apple`, `files`, `notion`. The test is CONTRIBUTING's: could a stranger hold
this input? A local Notes database, a directory of text, a Notion account — yes
to all three.

`files` carries more weight than its name suggests, and there is deliberately no
fourth adapter for Obsidian: **a vault is a directory of markdown**, which is the
format the engine has claimed to read since ADR-0001. Writing an `obsidian`
adapter would have meant a second copy of the same walk, differing only in the
four places a vault is not a loose pile of files — dot-directories of machinery,
binary attachments filed beside the prose, daily notes named for nothing but
their date, and `tags:`/`aliases:` properties. Those belong in the one walk, and
they are there. The same is true of anything else that exports markdown.

The test is **not** whether a source needs a credential. An earlier draft of this
decision read the format/place rule that way and concluded that Notion had to be
reached by its export, because the API needs a token and a per-page connection.
That is contradicted by the engine as it already stands: Trakt ships here with a
full OAuth refresh, Raindrop with a bearer token, collections with a Google
service account. A place is your city's venue calendars or your team's wiki
behind your SSO — something nobody else can point the same code at. A Notion
account is not that.

The practical half of it is worse than the theoretical half. **Notion has no
endpoint that triggers an export.** Producing one is a human opening the app,
clicking Export, waiting for an email and moving a zip out of Downloads — so an
export-only adapter can never be a step in a nightly. Notes are the freshest and
most-edited thing in the record; every other source here either self-refreshes or
reads a file that genuinely has no API behind it. Notes were one decision away
from being the only source that ran when somebody remembered.

So **the API is the default road**, and the export is the other road of the same
adapter rather than a second one. They agree on identity — Notion glues each
page's id onto its export filename, and it is the same dashless UUID the API
returns — so a page read either way lands as one note in one place, and an
instance can switch roads or use both without landing anything twice.

The export road keeps the two jobs it is genuinely best at: a one-time migration
off Notion, and reading the archive of a workspace nobody can reach any more. It
needs no network, which is also what lets the tests exercise it end to end.

### What each adapter must answer honestly

**`folder` is the folder axis of publication**, so an adapter answering it
carelessly is answering a privacy question carelessly. For a source with no
folder concept the honest answer is `""` — the unfiled drawer, which is held
(ADR-0009). A blob piped in on stdin is unfiled by definition. A file at the root
of a directory you dumped is unfiled, because dumping a directory is not filing
it.

**`external_id` must be stable across runs.** Notion's export puts the page id in
every filename, which is what lets a re-export be recognised as the same notes; a
page without one falls back to a hash of its path and the adapter says so out
loud, because renaming that page will land a duplicate and nothing downstream
could tell.

## Consequences

- Adding a source is one module: `LANDING`, `SOURCE`, `read(src)`. It cannot
  choose an id scheme, a filename, a frontmatter field or a landing path — those
  are the contract and the contract has one implementation.
- A note's carried-through frontmatter (a vault's tags and aliases) reaches the
  FILE but not the `t1_notes` payload. A note row's id hashes its payload values,
  so adding a column re-mints every note id — folding tags into the record is its
  own decision, taken deliberately or not at all. Until then they are on disk,
  greppable, and lost by nothing.
- Re-importing is cheap. Vectors key on `sha256(text)` (`exo/embed.py`), so a
  note whose text is unchanged reuses its vector even when its id, its path and
  its source have all changed. Moving silos costs a re-index, not a re-embed.
- A note that moves from the vault into the Exo tree gets a new row id, because
  the source string is part of it. That is a one-time cost of the cutover and it
  is the correct behaviour: a source string is data, not branding.
- Two trees holding the same relative path is refused rather than merged.
  `origin_ref` is the join key publication, atoms and both vector tables use; two
  notes sharing one is not a duplicate, it is a corruption.
- `exo ingest-notes` no longer means Apple Notes. Bare, it runs every source in
  `[notes.sources]`; named, it runs one.
- **One behaviour changed, and it is the kind CONTRIBUTING warns about.** The old
  Apple ingester wrote `folder: Unfiled` for a note with no folder; the contract
  writes `folder: ""`. `""` is the unfiled drawer that ADR-0009 holds, while
  `Unfiled` is a folder name that has to be separately declared and reads like a
  real place someone filed something. But `folder` is a payload value, so any
  note this affects gets a new row id: the ledger reads it as new, and the
  surface announces it as recently added. Apple gives every note a folder
  (`ZTITLE2` of its folder row, `Notes` for the default drawer), so in practice
  this is empty — which is why it is written down rather than worked around. An
  instance that finds otherwise should expect one noisy first import and a
  `note_folders` entry for `Unfiled` it can then delete.
- An adapter may be shown what is already landed (`seen`), so a source reached
  over a network can skip fetching a page it can already tell is unchanged.
  Notion's search returns `last_edited_time` for a request per hundred pages,
  while opening a page costs a request per hundred blocks — so a nightly over a
  settled workspace spends seconds rather than minutes. It is an optimisation and
  never a correctness mechanism: ignoring it is always right, an adapter that
  uses it must still hand every note back, and `--full` withholds it.
