# ADR-0001 — One store, scoped zones: DuckDB catalog over tier-native files

Status: accepted · 2026-08-08

## Context

Data across the personal-OS repos was reached into point-to-point: consumption
exports read twice (second-brain recaps + taste-engine taste), notes read by
hardcoded cross-repo path, three different provenance vocabularies for one
human/machine idea. We want one place to query `taste ⋈ notes ⋈ scrobbles`
without moving ownership of anything, and without a risk of model collapse.

Two hard constraints:
1. **Notes stay human-editable markdown under git** — second-brain's
   vault-is-the-record thesis is load-bearing. T1 can't be ripped into a DB.
2. **Local-first, single-user, small data** — no server.

## Decision

One **store** (this repo), one **query catalog** (DuckDB) over **tier-native
files**:

| Zone | Form | Write-scope |
|------|------|-------------|
| T0 external consumption | parquet | ingest loaders |
| T1 human-authored | markdown (second-brain, unmoved) + a rebuilt parquet index | you, by hand |
| T2 machine-derived | parquet, regenerable | derivation engines |

DuckDB is the **lens**, never the source of truth. Losing the catalog or any
parquet costs a rebuild, not data. Notes are **registered, not moved** — the
warehouse only ever reads the vault and materializes a projection.

Ownership is a **write-scope**, not a separate copy: a tool writes only its
zone, reads across all. There are no per-tool home stores.

## The wall (anti-collapse invariant)

Derivation reads the world through the **`source` catalog profile**, which
registers only `t0_*` and `t1_*` views. A derivation engine physically cannot
`SELECT` a `t2_` view — it does not exist on that connection. Every T2 row also
self-declares `grounds=false`. So machine output can never fold back into its
own inputs; T2 is always regenerable from T0+T1. `wh verify` proves the
regeneration is deterministic (identical digests across rebuilds).

This is the same posture second-brain's garden already had (regenerated every
tend, nothing cites in — ADR-0002) generalized to one store.

## Alternatives rejected

- **Single embedded DB as source of truth** (notes become rows): breaks
  markdown/git thesis; needs fragile bidirectional sync.
- **Postgres**: a server to run/secure; overkill for local-first single-user.
- **No DB, filesystem + in-memory index**: fine for lookups, but we need real
  cross-zone SQL joins.

## Consequences

- The store is now **load-bearing** — needs backups + a rebuild runbook
  (`wh backup`, README runbook). Per-repo offline independence is given up by
  design.
- Consumers cut over to the read API incrementally; old paths stay as fallback
  until byte-parity (see CUTOVER.md). No blind deletes of working repos.
