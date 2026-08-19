# ADR-0003 — Recipes are T1, meals are a T0 log; the ranking math stays out of the store

Status: accepted · 2026-08-09 · extends ADR-0001 (three zones, one lens, notes registered-not-moved) and ADR-0002 (structure is generated on ask, not materialized). Companion: taste-engine ADR-0001 (the cooking vertical that reads this).

## Context

The eating vertical (taste-engine ADR-0001) needs three things from a store it
does not own: **recipes** to rank, a **log of what you ate** to compute a variety
penalty from, and **ratings** to weight outcomes. The design doc
(`taste-engine/docs/eating-vertical.md`) named the tables; this ADR places them in
the tier model without breaking either the wall (ADR-0001) or the on-ask rule
(ADR-0002).

The tension is the usual one for this repo: it is tempting to store the *scores*
(variety penalty, outcome weight) next to the *facts*, because taste-engine will
ask for them together. ADR-0002 already answered that — derived structure is
generated on ask, never materialized — and this ADR holds that line for meals.

## Decision

**1. `recipe` is T1 — human-authored, registered not moved, indexed to parquet.**
A recipe is curated by hand and edited over time; that is T1's write-scope ("you,
by hand"), the same class as notes. It is *not* prose markdown, so it is a second
T1 **form** beside second-brain's vault: an authored structured file (the seed is
`soy_curl_bowls_enriched.json`) that the catalog rebuilds into a parquet index,
exactly as ADR-0001 rebuilds an index over notes. Canonical fields:

| Field | Note |
|---|---|
| `id`, `base`, `cuisine`, `flavor_group` | `flavor_group` is the variety-penalty axis |
| `sauce_ingredients[]`, `toppings[]`, `starch_base` | the components |
| `pantry_needs[]` | the future pantry-gate join key |
| `time_min`, `effort`, `sauce_mode`, `make_ahead[]`, `yield_servings` | cook-mode rank inputs (taste-engine ADR-0001 §1) |
| `allergy_safe_nut_free` | **data the taste-engine constraint reads; enforced there, never here** |
| `steps[]` | method |
| `source_ptr` | → the raw recipe note in second-brain, when one exists (registered, not moved) |
| `tested` | `false` for the enriched seed (times/steps derived, unverified); `true` once you've cooked and corrected it |

`source_ptr` keeps the ADR-0001 discipline: the store holds the structured
canonical, second-brain holds the origin prose, and nothing is copied out of the
vault.

**2. `meal_event` and `meal_rating` are T0 — an append-only consumption log.**
What you ate is a consumption event in the same class as scrobbles: observed,
append-only, written by an ingest loader (a meal logger), never hand-edited as
canon. Both live in T0 parquet.

- `meal_event`: `ts`, `mode` (`cook`|`out`), `recipe_id` | `place_id`, `who_with`,
  `cost`, `notes`
- `meal_rating`: `meal_event_id`, `score`, `redo?`, `tweak_notes`

A rating is a subjective observation *about* an event, not a derivation — it is
data you produce once and never recompute, so it is a log, not a T2 view.

**3. `pantry` is T1, deferred.** On-hand groceries are human-maintained mutable
state (you edit the list), so T1 by write-scope — but it is neither stable canon
nor an append log, and the cooking vertical treats it as advisory until v2
(taste-engine ADR-0001 defers the pantry gate). Named here so it has a home; not
built now.

**4. The catalog exposes the join; it does not store the scores.** taste-engine
reads `recipe ⋈ meal_event ⋈ meal_rating` through DuckDB (ADR-0001's lens). The
**variety penalty and outcome weight are computed in taste-engine's `rank`, on
ask** — they are exactly the kind of derived structure ADR-0002 refuses to
materialize. The warehouse holds facts (recipes, events, ratings); it never holds
a "recipe score" column. Losing the catalog costs a rebuild from these three, not
data.

## Consequences

The eating vertical gets a queryable store with a clean tier story: recipes
rebuildable from an authored file, meals reconstructable from an append log,
nothing derived left lying around to reason from by accident. The wall holds —
the only human-authored canon is the recipe file and the notes it points at, both
T1; everything the machine wants (scores, penalties) is regenerated downstream.

The seam to watch is **T1's second form**. ADR-0001's T1 was "markdown +
index"; recipes make it "markdown **or** authored-structured + index." That is a
widening of T1, not a breach — the write-scope ("you, by hand") and the
regenerable-index contract both still hold — but a future reader should know T1 is
now a *class of authored sources*, not specifically the vault. If a third authored
form shows up, that is the moment to generalize the T1 loader rather than special-
casing a third time.

Deferred, and named: **pantry** (§3) waits for v2. **The meal logger** — whatever
writes `meal_event` (manual CLI, calendar sync, a share-sheet) — is an ingest
loader to be specified when the vertical is built; this ADR fixes the *schema* it
must write, not the loader. **`tested`-gated ranking** — taste-engine may choose to
downrank or flag `tested = false` recipes; that policy is the vertical's, the flag
is ours.

Rejected: **recipes as T0** — they are authored and edited, not externally
ingested; T0's write-scope is loaders, not you. Rejected: **a materialized
`recipe_score` / `variety` T2** — ADR-0002 already ruled that structure is
on-ask; a stored meal score is one more projection that hardens into a thing you
reason from. Rejected: **50 hand-written recipe notes in second-brain as the
canonical form** — the seed is a bulk structured import; forcing it into 50 prose
notes fights the data's shape. Prose lives in `source_ptr` when it exists; the
structured file is canon.

## Amendment — 2026-08-10: recipes carry a `source`; the blog is the third authored form

The seed was synthetic (50 soy-curl bowls, all `tested:false`) — a cold-start
template, not the user's cooking. This amendment wires the real recipes from the
blog (the mirrored `_posts`, frontmatter `type: recipe`) into the same
`t1_recipe` zone, taking the generalization the original decision named as
"the moment": T1 recipes are now a *class* of authored sources, not one file.

**Schema widened (additive, back-compatible):** `t1_recipe` gains `title`,
`ingredients` (flat JSON list — real recipes don't decompose into
sauce/toppings), `source_url`, and `is_seed`. The bowl-shape fields
(`base`, `starch_base`, `sauce_mode`) stay in the schema but are empty for blog
recipes; `flavor_group` is the cuisine (each post its own variety axis).

**Two loaders, one zone.** `recipes()` (seed, `is_seed=true`) and the new
`recipes_blog()` both emit into `t1_recipe`; `io.write_zone` unions their payload
keys. Blog recipes are published, so `tested=true`, and cite their post URL.
`recipe_id` is 1000+ for blog rows (assigned by sorted filename) so it never
collides with the 1–50 seed. Config: `[paths].posts`.

**Ranking policy (the vertical's, per the original "`tested`-gated ranking"
note):** taste-engine boosts real (`is_seed=false`) recipes over templates and
cites the source URL; cocktails/mocktails are indexed but filtered out of
cook-meal picks. Nothing about the schema forces this — the flag is ours, the
policy is the vertical's, as this ADR always held.
