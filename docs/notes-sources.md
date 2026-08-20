# Notes: where they come from, and how to add a source

The decisions behind this are [ADR-0017](adr/0017-a-note-source-is-an-adapter.md)
(the adapter contract) and
[ADR-0018](adr/0018-the-notes-tree-is-the-original-and-the-bucket-is-a-mirror.md)
(where the tree lives). This is the how-to.

## What ships

```bash
exo ingest-notes                     # every source in [notes.sources]
exo ingest-notes notion              # the API — incremental, safe in a nightly
exo ingest-notes notion --from x.zip # a Markdown & CSV export — offline
exo ingest-notes apple               # the local NoteStore.sqlite (needs Full Disk Access)
exo ingest-notes files --from ~/vault
pbpaste | exo ingest-notes files --from -
exo ingest-notes notion --full       # re-read everything, ignoring what is landed
```

`files` is the one that covers most of what people actually have. A directory of
markdown or text is the format the engine has claimed to read since ADR-0001, so
**an Obsidian vault needs no adapter — it is already one of these.** The four
things a vault does that a loose pile of files does not are handled:

| a vault does this | and `files` |
|---|---|
| `.obsidian/`, `.trash/`, `.git/` | skips every dot-directory |
| `attachments/*.png` | reads only text suffixes, and refuses anything holding a NUL |
| `2026-02-03.md` daily notes | dates the note from the filename, separator or not |
| `tags:` / `aliases:` properties | carries them into the landed file, block or flow style |

Anything else that exports to markdown lands the same way — Bear, Ulysses, iA
Writer, a Logseq graph, a folder somebody rsynced off a dead laptop. A tool whose
properties are not YAML frontmatter (Logseq's `::`) keeps them in the body as
text rather than losing them.

Two caveats, both by design rather than by omission:

- **A template folder is a folder.** `templates/` lands as `folder: templates`
  and, like every folder, must be decided in `serve-manifest.json` before
  anything publishes. Reading a vault's config to guess which folders are
  furniture would be one app's convention baked into the engine.
- **Wikilinks pass through verbatim.** `[[On caching]]` is stored exactly as
  written. Rewriting a link on the way in would make the copy a different
  document from the one you wrote.

## The contract

A note file is markdown with the frontmatter the record reads:

```
---
type: raw            # raw is thinking, refined is finished prose
created: 2026-02-03  # when it was written
imported: 2026-08-20 # when it arrived
source: notion       # which silo — data, not branding
uuid: <source id>    # identity, so a re-import is idempotent
folder: Reading      # the FOLDER AXIS of publication
title: On caching
---
```

Anything else a source file carried rides along beside those. A carried key that
spells a contract key is prefixed `src_` rather than dropped — it may not
overrule the contract, and it may not be lost either.

## Writing an adapter

Three names. That is the whole interface.

```python
"""Where these notes come from, and what is true about them that is not
true about notes in general."""
from exo.notes import SourceNote

LANDING = "wiki"     # notes/raw/wiki/ — its OWN directory, always
SOURCE = "wiki"      # goes into every row id; choose once, never rename

def read(src=None, seen=None):
    return [
        SourceNote(
            external_id=page["id"],       # stable across runs, unique in this source
            title=page["title"],
            body=page["markdown"],
            created=page["written_on"][:10],
            folder=page["space"],         # "" if the source has no folders
        )
        for page in fetch(src)
    ]
```

`src` is whatever `--from` gave, or the `[notes.sources]` value, or `None`.

`seen` is optional, and both local adapters leave it off. It is what is already
landed, keyed by `external_id`, so a source reached over a network can skip
fetching a page it can already tell is unchanged — Notion's search returns
`last_edited_time` for one request per hundred pages, while opening a page costs
one request per hundred blocks. It is an optimisation and **never** a correctness
mechanism: ignoring it is always right, and an adapter that uses it must still
hand every note back, because an omitted note is absent rather than unchanged.

### The two you have to get right

**`folder` is a privacy decision.** It is one of the two axes publication gates
on, so answering it carelessly is answering a privacy question carelessly. If the
source has no folder concept, the honest answer is `""` — the unfiled drawer,
which is held ([ADR-0009](adr/0009-the-unfiled-drawer-is-held.md)). A blob piped
in on stdin is unfiled by definition.

**`external_id` must be stable across runs.** It is how a re-import recognises a
note it already has. If the source has no id, a path is a reasonable one and a
hash of the text is a reasonable one; a value that changes when the title changes
is not, and will land a duplicate the first time somebody renames something.

### The first publish will refuse, and that is the point

Path zones match by longest declared prefix, so a source landing inside an
existing zone would inherit that zone's serve decision. Each source gets its own
top-level `notes/raw/<landing>/`, which nothing prefixes — so the next publish
stops with *"note(s) under no declared path_zone"* until you add it to
`serve-manifest.json` and say `serve` or `hold`. A new source is a new question
about what leaves your machine, and it is not answered by a decision you made
about a different one.

## Engine or instance?

The same test as any loader ([CONTRIBUTING](../CONTRIBUTING.md)): **could a
stranger hold this input?** A directory of markdown, a Notes database, a Notion
account — yes, so those ship in the engine.

Needing a credential is *not* what makes something a place. Trakt ships here with
a full OAuth refresh, Raindrop with a bearer token. Your team's wiki behind your
SSO is a place; put it in your instance:

```python
# $EXO_HOME/plugins/wiki.py
NOTE_SOURCES = {"wiki": my_adapter_module}
```

It gets the same landing rule and the same fail-closed publication consequence as
the built-in three.
