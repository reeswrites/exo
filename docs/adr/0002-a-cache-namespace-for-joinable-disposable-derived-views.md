# ADR-0002 — A cache namespace: joinable, disposable derived views that carry no rebuild contract

Status: accepted · 2026-08-09 · extends ADR-0001 (adds a namespace beside the tiers; does not touch the wall or T2's contract)

## Context

ADR-0001 set three tiers and a wall. T0/T1 are grounds; T2 is machine-derived,
`regenerable = true`, and **verify-bound**: `wh verify` blows away a T2 parquet,
rebuilds it from T0+T1, and digests the result to prove the store holds nothing
you can't reconstruct. That guarantee is load-bearing for `atom` — everything
grounds on atoms, so they must be reconstructable. `t2.py` went further and said
structure (themes, returns, collisions) is generated **on ask**, never
materialized — a stored structure tier is one more place a projection hardens
into a thing you reason from (the ADR-0028 seduction).

A real consumer now presses on that. Returns (alchemy, ADR-0035) traces the
concepts the owner keeps circling back to, and it wants **joining against taste and
notes** — `returns ⋈ t0_music ⋈ t1_notes`, score a concert by artists on a live
trail. On-ask-only can't be joined. So today Returns writes a markdown map to
alchemy's `out/returns/` — a loose file *outside the store*: unjoinable,
uncatalogued, invisible to everything.

But making it a full T2 tier over-solves the problem. Returns is **disposable by
its own design** (ADR-0035, ADR-0028: remade never edited, nothing cites it, it
is a take not a record). Binding it to `wh verify` would demand it rebuild
*identically*, which:

- forces determinism Returns doesn't otherwise need, and
- when the engine lives in another repo (alchemy — ADR-0034/0035), forces
  `wh verify` to shell back into that repo to regenerate — the integrity checker
  taking a dependency on the thing it checks.

That is a lot of contract for output you'd just rerun. The tiers give two
postures — *grounds you can't lose* and *derived-but-verify-bound* — and neither
fits "joinable, disposable, rerun-to-refresh." Between the loose file (in no
store) and the T2 tier (full rebuild contract) there is no middle. This ADR adds
it.

## Decision

Add a **cache namespace** to the store: `zones/_cache/*.parquet`, surfaced in the
catalog as `cache_<name>` views, **registered in the `full` profile only**.

| | T2 tier (ADR-0001) | **cache namespace (new)** |
|---|---|---|
| Home | `zones/t2/` | `zones/_cache/` |
| Catalog view | `t2_<name>` | `cache_<name>` |
| Visible to `source` profile (the wall) | no | **no** |
| Visible to `full` profile (joins/display) | yes | **yes** |
| `wh verify` rebuild contract | **yes** — must regenerate identically | **no** — whatever the engine last wrote |
| On loss | proven rebuild from T0+T1 | **rerun the engine; not a backup concern** |
| Envelope | `grounds=false, regenerable=true`, stamped | `grounds=false`, stamped; `regenerable` is not policed |
| Use | the durable substrate (atoms, vectors) | disposable derived views (returns, collisions) |

A cache view is a materialized on-ask computation. It is in the store, so it
**joins in DuckDB exactly like a tier** (`t0_music JOIN cache_returns …`). It
carries **no** rebuild guarantee: it is whatever its engine last wrote, refreshed
by rerunning that engine. `returns` and `collision` are the first two cache
views.

This does **not** reverse `t2.py`'s "no stored structure *tier*." That still
holds — structure is not a tier. A cache is a different thing: not verify-bound,
not part of the backup/reconstruct contract, explicitly droppable. We are adding
a place for disposable joins, not promoting structure to a grounded tier.

## The wall is untouched — and that is what keeps this safe

`cache_*` views register in the `full` profile only. The `source` profile (the
wall) registers **nothing** from `_cache`. So a derivation engine — atomizer,
vectorizer — connecting with `profile="source"` **structurally cannot SELECT a
`cache_` view**; it does not exist on that connection. Machine-made cache output
therefore can never re-enter grounding derivation. Same mechanism ADR-0001
already relies on, extended to the cache by the same "enforced by absence" law.

One soft discipline remains, and its stakes are lower than a tier's: **an engine
should not read its own cache view.** Returns reads `t2_atom` + `t2_atom_vec`,
never `cache_returns`. This keeps each run a function of the substrate, not of
its own last output (drift). It is **not** catalog-enforced — a cache-writing
engine reads the `full` profile, which includes cache views — so it is held by
the engine's query surface and by review. Crucially, the worst case if it's
broken is *the cache view drifts in quality*, **not** *the corpus collapses* —
because nothing grounds on a cache. The wall protects the grounds; the cache
lives downstream of it.

## Why this lets the engine stay in another repo

Because a cache view carries no rebuild contract, **`wh verify` never regenerates
it**, so warehouse never needs to reach into the engine that wrote it. The
dependency stays one-way: an engine in alchemy imports warehouse to read
(`api.read`) and to write the cache; warehouse imports nothing back and shells
nothing out. The fragile cross-repo verify seam a T2 tier would have forced
simply does not exist here. (See alchemy ADR-0036.)

## Small changes this needs

- `config.CACHE = ZONES / "_cache"`; `ensure_dirs` covers it (the dir exists).
- `catalog._views`: when `profile == "full"`, also glob `_cache/*.parquet` and
  register each as `cache_<stem>`. Never for `profile == "source"`.
- A write path for cache: either extend `io.write_zone` to accept a `"cache"`
  pseudo-tier (→ `config.CACHE`), or a thin `cache.write(name, rows)`. Rows are
  stamped `author=machine, grounds=false`, `source=<engine>` (e.g.
  `alchemy.returns`).
- `wh verify` is **unchanged** — it walks `zones/t2/` only. Cache is out of
  scope by construction. Say so in the verify docstring so its silence on cache
  is intentional, not a gap.
- `wh backup`: cache is droppable; it need not be backed up (rebuild = rerun the
  engine). Exclude `_cache` from backups, or back it up as a convenience only.

## Alternatives rejected

- **Loose `out/` file (status quo).** Not in the store → unjoinable, uncatalogued.
  The thing this ADR exists to kill.
- **Full T2 tier (the earlier draft of this ADR).** Verify-bound, so it forces
  determinism and — for a cross-repo engine — a subprocess regeneration seam that
  couples `wh verify` to a sibling repo. All that contract for output that is
  disposable by design. Rejected: pay for a guarantee the data doesn't need.
- **Register cache in the `source` profile too.** Would let derivation read
  cache views → reopens collapse. The full-profile-only restriction is the whole
  safety story; it is not negotiable.
- **A DuckDB table instead of a parquet view.** Tables live inside the catalog
  file; the store's thesis is tier-native parquet the catalog only *lenses*.
  Keep cache as parquet so losing the catalog still costs only a rebuild.

## Consequences

- The store gains a third surface: grounds (T0/T1), verify-bound derived (T2),
  and **disposable derived (cache)**. Joins across all three work in the `full`
  profile; the wall still sees only grounds.
- `returns`/`collision` become `cache_*` views — joinable against taste and
  notes, which the loose file never allowed. That is the payoff, at the cost of a
  ~10-line catalog change and a cache writer.
- `wh verify`'s scope and the anti-collapse guarantee are **unchanged**. Cache is
  deliberately outside the rebuild contract.
- New standing risk, lower-stakes than a tier's: the "don't read your own cache"
  rule is review-enforced, not catalog-enforced. Every cache-writing engine's
  read query must be auditable against it.
- Cache can be **stale**. That is acceptable only for output that is
  rerun-to-refresh by design. Do not put anything in `_cache` that something
  else must trust to be current; that belongs in T2 (with the contract) or T1.
