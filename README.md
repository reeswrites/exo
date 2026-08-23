# Exo

**A personal context layer for agentic life systems.**

Exo — short for *exocortex*, the part of your thinking that lives outside your
head — is one record of a life, in a shape you own. Everything you consume,
everything you write, and everything a machine concluded from the two, in one
place with one SQL surface across it.

A filtered copy of that record is published to a **read-only MCP server**, so
whatever assistant is in front of you can use it as context — without that
assistant's vendor coming to own the record.

```
loaders ─────────▶   the record   ─────────▶   the surface
what reaches it    what it holds          what it exposes
(one per source,   (tier zones, one       (a fixed set of named
 each writes one    SQL surface, one        questions, read-only
 zone)              wall)                   by construction)
```

## Why

You should own your personal context, your operational knowledge and your
memory as far as you can. That is what makes them portable, and it is the only
thing that makes fine-grained privacy possible at all.

- **You own the routine without owning the scheduler.** Self-hosting everything
  is admirable and not pain-free; sometimes someone else's infrastructure is the
  right answer. What should not move is the record.
- **No platform lock-in, no model lock-in.** The record is parquet and markdown
  on your disk. The surface is MCP, which every assistant speaks.
- **Meet you where you work.** A centralised store reachable over MCP means the
  same context reaches your terminal agent, your editor and your phone.

This exists because the alternative is a dozen silos that each know a tenth of
you and none of which will give it back.

## What it actually does

**Loaders** pull your data out of the silos it is trapped in: Last.fm,
Letterboxd, Goodreads, Untappd, Trakt, Raindrop, your Claude and ChatGPT
exports, your markdown notes, your published writing, your git repos. Each
loader writes exactly one zone and nothing else.

**Notes get their own front door**, because notes are the one thing that keeps
changing address. `exo ingest-notes` reads a *note source* — Apple Notes, a
Notion export, a directory of text files, a blob on standard input — and lands
each note as markdown you own, with the frontmatter the record reads (ADR-0017):

```bash
exo ingest-notes notion                     # the API, incremental, nightly-safe
exo ingest-notes notion --from Export.zip   # or an export, offline
exo ingest-notes files  --from ~/writing
pbpaste | exo ingest-notes files --from -   # a blob; its first line is the title
```

`files` covers more than it sounds like: a directory of markdown is the format
the engine has read since ADR-0001, so **an Obsidian vault needs no adapter** —
its dot-directories, attachments, bare-date daily notes and `tags:` properties
are all handled. Adding a genuinely new source is one small module, and
everything downstream — atoms, vectors, both publication axes, the read surface —
never learns which one it was. [docs/notes-sources.md](docs/notes-sources.md) is
the how-to.

**The record** keeps them separated by *who may write*, not by what the data is:

| tier | what it is | written by |
|---|---|---|
| **T0** consumption | what the world recorded about you — scrobbles, ratings, saves | loaders only |
| **T1** authored | your words: notes, posts, verdicts, recipes, tasks | you, by hand |
| **T2** derived | what a machine concluded: atoms, vectors, affinities | derivation only |

Derivation reads T0 and T1 and **physically cannot see T2** — the profile it
reads through has no T2 views at all. So T2 always regenerates from ground
truth, and `exo verify` proves it by deriving twice and comparing digests.
Machine output never becomes machine input.

**The surface** publishes a *physically separate* copy containing only rows you
declared publishable. Held material is absent from the published files, not
filtered at read time, so no bug downstream and no injected instruction can
reach it. The policy is fail-closed: an undeclared zone fails the build rather
than defaulting either way.

**The surface is also modular.** An instance offers a subset of the engine's
tools, resolved when you publish (ADR-0020). A tool whose zones you hold retires
itself rather than being advertised and then failing on a table that is not
there; `[tools]` in `exo.toml` switches off the rest by domain or by name. That
second lever is for when something else you connect answers the question better
— because Exo is not meant to be the only MCP server you run:

```toml
[tools]
domains = ["mind", "workshop"]   # specialise this instance
disable = ["recipes"]            # a peer answers this one better

[peers]
sources = { notion = "Notion" }  # these notes are also live over there
```

A **peer** is a server you also connect that holds the same material live. Exo
keeps serving it — the filed, indexed, publication-graded copy is the half it is
good at — and says on the row which peer holds the current one, so an assistant
reading both can tell one note from two instead of quoting the same paragraph
twice. What to do about it is the agent's call, not ours (ADR-0013).

## The engine and your instance

This repository is the **engine**. It holds no data and never will.

Your data, your config and your own loaders live in an **instance** — a
separate, private directory (usually its own private repo) that the engine
finds through `$EXO_HOME`:

```
your-instance/
  exo.toml              who you are, where your inputs live
  serve-manifest.json    what may leave this machine
  raw/                   your exports and mirrors
  notes/                 notes you own, landed by `exo ingest-notes`
  zones/                 the record itself
  plugins/               loaders only you could have
  items/                 your tasks, habits, slots
  procedures/            how you do a recurring thing, by hand
```

A loader ships in the engine if its input is a **format** — a Last.fm export, a
folder of markdown, a directory of git repos. Anyone can hold one of those. A
loader belongs in your instance if its input is a **place**: your city's venue
calendars, your sibling repos, your blog. The test is not how specific the code
is; it is whether a stranger could hold that input at all.

## Start

```bash
uv sync
uv run exo init ~/my-exo        # scaffold an instance
export EXO_HOME=~/my-exo
# drop your exports into $EXO_HOME/raw/exports/, then:
uv run exo rebuild              # ingest -> index -> derive -> catalog
uv run exo query "SELECT artist, count(*) FROM t0_music GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
uv run exo publish --dry-run    # what would leave, and what is held back
```

The templates `exo init` copies are [exo/templates/](exo/templates/) — read
them before you edit the copies.

## Reach it from your phone

The point of the read surface is that the record answers when you are nowhere
near the machine holding it. [worker/](worker/) is a Cloudflare Worker speaking
MCP over HTTP — read-only permanently, serving only the publication bundle — and
[worker/README.md](worker/README.md) walks through standing it up and connecting
it to [Poke](https://poke.com), the iMessage assistant:

```sh
cd worker
cp wrangler.example.toml wrangler.toml       # your database id, your buckets, your name
npx wrangler d1 create exo                   # + r2 bucket create exo-vectors
npx wrangler deploy
npx wrangler secret put AUTH_TOKEN           # openssl rand -hex 32
```

then publish a bundle into it (`exo publish --cf`, then `import.sh`), and add
the Worker's URL and that token at
[poke.com/integrations/new](https://poke.com/integrations/new). Any MCP client
works the same way — Poke is just the one that reaches you by text message.

Then read [CONTEXT.md](CONTEXT.md) — it is the vocabulary, and the rest of the
repo assumes it.

## Honest limits

- **The startup cost is real.** This is a tool for someone who already keeps
  notes, already exports their data, and already wanted this. It will not
  bootstrap the habit for you.
- **Loaders break.** They read other people's export formats and scrape other
  people's pages. Each one keeps its last good file rather than overwriting it
  with a failure, but a rotted loader is a matter of when.
- **It is a second brain underneath.** A personal wiki, loaders for consumption
  data and archives, some distillation on top. The novel part is not any one of
  those; it is that they share one record with one publication boundary.
- **The chat-export loader is a pocket knife.** It normalises Claude and ChatGPT
  exports into one shape, which is useful whether or not you want the rest of
  this.

## The decisions

Every non-obvious choice has an ADR in [docs/adr](docs/adr). The load-bearing
ones:

| | |
|---|---|
| [0001](docs/adr/0001-duckdb-catalog-over-tier-native-files.md) | a DuckDB catalog over tier-native files, and the wall |
| [0005](docs/adr/0005-split-the-etl-laptop-ingests-cloud-rebuilds.md) | split the ETL: the laptop ingests, the cloud rebuilds |
| [0006](docs/adr/0006-the-remote-read-surface-is-permanently-read-only.md) | the remote read surface is permanently read-only |
| [0007](docs/adr/0007-bound-the-blast-radius-of-an-injected-read.md) | bound the blast radius of an injected read |
| [0014](docs/adr/0014-the-code-is-public-the-instance-is-private.md) | the code is public, the instance is private |
| [0016](docs/adr/0016-procedures-are-resources-not-tools.md) | a procedure is a resource, not a tool |
| [0017](docs/adr/0017-a-note-source-is-an-adapter.md) | a note source is an adapter, and the note file is the contract |
| [0018](docs/adr/0018-the-notes-tree-is-the-original-and-the-bucket-is-a-mirror.md) | the notes tree is the original; the bucket is a mirror |
| [0019](docs/adr/0019-publicity-is-an-axis-not-an-adjective.md) | publicity is an axis, not an adjective |
| [0020](docs/adr/0020-the-surface-is-modular-and-a-peer-is-not-a-competitor.md) | the surface is modular, and a peer is not a competitor |

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) first — particularly the part about
never putting personal data in this repository, including your own, and the
list of small-looking changes that re-mint every id in a running instance.

MIT.
