# Contributing

Exo is the engine. It holds no data, and it must stay that way.

## The one rule

**Never put personal data in this repository** — not in an issue, not in a test
fixture, not in a screenshot, not in a commit message. That includes yours.

If you are reporting a bug in a loader, the useful report is the *shape* of the
input that broke it: a hand-written three-line file that reproduces the failure,
not an export of your own account. If a bug only reproduces with real data, say
so and describe the shape; someone will build a fixture.

`tests/test_no_personal_strings.py` enforces the machine-checkable half of this
— a home directory, a name, a hostname, a cloud resource id anywhere in the
engine tree fails CI. The rest is judgement, and reviewers apply it.

## What belongs in the engine

A loader belongs here if its input is a **format**: a Last.fm export, a
Letterboxd CSV, a folder of markdown, a directory of git repos. Anyone can hold
one of those.

A loader belongs in *your instance*, under `plugins/`, if its input is a
**place**: one city's venue calendars, one sibling repo, one person's blog.
Specificity is not the test — whether a stranger could hold that input at all is
the test. See [ADR-0014](docs/adr/0014-the-code-is-public-the-instance-is-private.md).

The same rule decides a **note adapter** (`exo/notes/sources/`, ADR-0017), and
needing a credential is **not** what makes something a place — Trakt, Raindrop
and the collections fetch all need one and all ship here. A Notion account is a
thing a stranger can hold, so `notion` ships; your team's wiki behind your SSO
does not, so it belongs in your instance under `NOTE_SOURCES`. Either way the
interface is the same five fields, and an adapter that reaches past them —
choosing a filename, an id scheme, a landing path — is reimplementing the
contract rather than answering it.

## Things that look like small changes and are not

- **`source=` strings.** Every row id is a hash of its content *and* its source
  string. Renaming one re-mints every id built from it: the ledger reads them as
  new, and a running instance publishes a life's worth of "recently added". If a
  source string has an ugly name, it keeps it.
- **Anything a payload value feeds.** Same reason. A change to how a facet or a
  voice label is computed rewrites ids for everything already stored.
- **The wall.** Derivation reads T0 and T1 and physically cannot see T2. If a
  change needs T2 as an input, the change is wrong — `exo verify` will say so.
- **The publication path.** `serve-manifest.json` is fail-closed on purpose: an
  unlisted zone fails the build rather than defaulting either way. A patch that
  makes it default is a patch that publishes things by forgetting them.
- **Where a note source lands.** Path zones match by longest declared prefix, so
  a new adapter landing inside an existing zone silently inherits that zone's
  serve decision. Each source gets its own top-level landing directory; the
  build failing on the first import is the feature (ADR-0017).

## Running it

```bash
uv sync
uv run pytest tests/ -q          # engine tests, against fixtures/
uv run exo verify                # the determinism proof
```

Every test runs against `fixtures/`, never against a real instance. If you need
a new fixture, write it by hand.
