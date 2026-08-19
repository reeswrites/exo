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
  zones/                 the record itself
  plugins/               loaders only you could have
  items/                 your tasks, habits, slots
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

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) first — particularly the part about
never putting personal data in this repository, including your own, and the
list of small-looking changes that re-mint every id in a running instance.

MIT.
