# ADR-0014 — Exo: the code is public, the instance is private

Status: accepted · 2026-08-19

## Context

This repo is two things wearing one name. It is an *engine* — tier zones, the
wall, a DuckDB catalog, loaders, a fail-closed publication step, a read-only MCP
surface — and it is *a life*: 40k scrobbles, 2k notes, a held-folder list that
names his journaling, eight scrapers that only make sense in Washington DC.

The engine is worth giving away. The premise behind it — that you should own your
personal context, operational knowledge and memory, so that no platform or model
owns the routine you built — is a claim you can only make credibly if the thing
making the claim is inspectable. The life is worth giving away to nobody.

Every scheme that keeps both in one repository degrades to vigilance: an
allowlist that has to stay correct, a `.gitignore` that has to stay ahead of a
new writer, a reviewer who has to notice that a fixture is real. Vigilance is the
same failure mode the serve projection was built to eliminate (ADR-0005): what is
held must be *absent*, not merely unreturned. The privacy boundary of the source
tree should be the same kind of boundary.

The name also stops fitting. "Warehouse" was chosen when the problem was
consolidation — get every pile into one building and stop reaching into siblings.
That problem is solved. What the thing *does* now is hold context on behalf of a
person and hand it to whatever agent is in front of them: an exocortex. A
warehouse implies inventory and throughput. What is actually being optimised is
recall.

## Decision

**1. Two repositories, and the instance is the unit of privacy.**

- Public `exo` — the engine. Package, worker, docs, synthetic fixtures, `exo init`.
- Private `exo-me` — one person's instance. `raw/`, `items/`, `notes/`,
  `captures/`, `zones/`, `serve-manifest.json`, the nightly workflow, the
  secrets, the machine paths, the DC scrapers, the ADRs that are about him.

Not a directory in a shared repo. A separate repository, so a personal file
cannot arrive in the public one by being filed wrongly — there is no path from
here to there.

**2. The dependency points one way and only one way.** `exo-me` depends on `exo`
at a pinned tag. `exo` does not know instances exist; it discovers one at runtime
through `EXO_HOME` and refuses to run without one. This is the same shape as the
rule the colocation migration found — *warehouse depends on nothing; everything
else reads from warehouse* — applied one level up.

**3. Core if the input is a format; instance if the input is a place.** A Last.fm
CSV, a Letterboxd export, a Goodreads dump, a markdown vault, a folder of git
repos — anyone can hold one of those, so the loader is core. Suns Cinema's
Filmbot API, Partiful's DC feed, the Living Room's calendar, a taste-engine
checkout — those read *his* city and *his* siblings, so they are instance
plugins. This is the rule that forces the loader registry to become a real
extension point rather than an import list, which is the modularity the project
claims to have.

**4. Secrets live only where data lives.** The public repo holds no secrets, no
account ids, no bucket names, and no `pull_request_target`. A stranger's fork can
therefore run the whole public test suite and exfiltrate nothing, because there
is nothing in scope to exfiltrate. Cloudflare ids move to `wrangler.toml` in the
instance; the public repo ships `wrangler.example.toml`.

**5. The public repo starts at commit one.** `zones/_snapshots/raindrop.json` and
`expression-gaps.md` are the only personal files ever committed here, but a
scrub is only as good as its audit, and the audit would have to cover every
historical revision of the README's row counts and the manifest's held-folder
names. Fresh history costs the commit prose — which stays readable in `exo-me`,
where it was always going to be more useful anyway.

**6. The rename is shell-deep.** `warehouse/` → `exo/`, `wh` → `exo`, `WH_*` →
`EXO_*`, and the storage vocabulary in the prose gives way to the exocortex one.
The tier names stay: `t0` / `t1` / `t2` describe *who may write*, not what kind
of building this is, and they are already the least metaphorical thing in the
glossary. `zones` stays and stops being an accident — a cortex has regions.

**7. Stored `source` strings do not change.** `stable_id` hashes `source` into
every row id (`provenance.py:29`), so renaming `warehouse.derive` →
`exo.derive` would silently re-mint every id in T2 and the caches: the
`first_seen` ledger would read them as new, the brief would announce a life's
worth of "recently added", and the D1 reconcile would replace whole tables in a
single night. Those seven strings are stored identity, not branding. They keep
their spelling and gain a comment saying why.

## Consequences

The public repo can be run by a stranger on day one only if the fixtures are real
enough to exercise the wall, the publication guard and the determinism proof —
so a synthetic instance stops being a nicety and becomes the public CI's only
input. That is the right pressure: everything the fixtures cannot reach is
something the engine had no business knowing.

Development gains a version bump. A change to the engine lands in `exo`, gets a
tag, and the instance's pin moves. Locally that disappears behind an editable
install (`uv pip install -e ../exo`), but the nightly is honest about it: it
builds against a pinned tag, so a bad engine commit cannot reach production data
by being pushed.

Two ADRs become public and lose their subject: 0009 (the unfiled drawer is held)
and 0011 (his repos are a source) generalise cleanly. 0004, 0008 and 0012 —
second-brain's deprecation, allergies, his blog — stay in `exo-me`, because they
are decisions about a life rather than about an engine.
