# Plan — ADR-0026: the D1 load becomes a diff

**For:** the session that owns `exo/scripts_impl/publish_cf.py` and the `import.sh`
it emits.
**Goal:** a nightly that writes the delta rather than the corpus, with every
publication guarantee in ADR-0005 unchanged.
**Not in scope:** the paid plan, and whether 41,294 scrobbles belong in D1 row by
row. Both are named under "what this does not decide" in the ADR and neither is
blocked by this work.

---

## The shape of the plan, in one line

**The big saving needs no key, no upsert and no row-level diff.** Skipping the
tables that did not change is a *skip*, not a merge: a table is either rewritten
exactly as it is today, or not touched at all. On 2026-09-01 that alone would
have taken the load from ~86,981 rows written to ~3,819. Everything harder —
keys, per-row diffs, updates — is phase 3, and phase 3 is optional.

*Phases 0, 1, 2 and 4 are shipped as of 2026-09-06. Phase 3 is not started and is
narrower than this document first thought — read phase 1's answer before opening
it.*

Do the phases in order. Each one is useful shipped alone.

---

## State of play — do not redo

- **`a74d25d` already removed the `t0_music` artist index.** A nightly measured
  **87,419** rows written on 2026-09-03 against a 100,000/day limit. Production is
  not on fire; what remains is that a single notes-lane fire (~42,000) can still
  put a day over, and that a sixty-row delta costs a five-figure write bill.
- Every row carries a content-derived `id` (`provenance.py:23`), and `publish`
  already treats `origin_ref` as the join key throughout (`publish.py:20`).
- The bundle already ships `expected-counts.txt` and `verify.sql`, and
  `import.sh` already reads them back, repairs a mismatch one file at a time,
  and refuses after three rounds. **This machinery is what makes a diff safe to
  ship. Do not weaken it to make a diff pass.**
- The reconcile (`served-tables.txt`), the shrink guard, and the partial/full
  scope rule are unchanged by every phase below.
- Vectors never reach D1 (`publish_cf.py:598`). This is entirely about the 34
  tabular tables.

## Two facts the design has to obey

**1. D1 reads back stale for seconds after a bulk load.** Recorded in
`import.sh`'s own comments: on 2026-08-19 six tables verified as EMPTY, the
repair reapplied all six, and five of them had been full the whole time. So:

> The read that COMPUTES a diff happens before the run writes anything. Never
> diff against a read taken after a write in the same run.

The read that *verifies* keeps the existing discipline — reapply, pause, look
again, three rounds.

**2. A stubbed wrangler proves nothing.** Two bugs passed the stub in
`tests/test_publish_scope.py` and failed against the real thing: `UNION ALL`
over 24 tables is rejected outright ("too many terms in compound SELECT"), and
`--file` is the bulk import path that never returns the rows a `SELECT`
produced. Both are in the comments now because both shipped. Every phase here
ends against real D1, not against the stub.

---

## Phase 0 — see the meter ✅ SHIPPED 2026-09-01

**What:** `apply_file()` already captures wrangler's JSON and discards it on
success. Parse `rows_written` out of it, total across batches, print one line at
the end of the import, and put it in the nightly's step summary.

**Why first:** every phase below claims a saving. Today the claim is arithmetic
— mine, in the ADR — and not a measurement. This makes it one.

**Verify:** one nightly. The total should land near 86,981.

**Result, 2026-09-03:** `87,419 rows written`. 438 above the prediction, and the
438 is the night's own delta. Under the 100,000 limit, down from ~128,275.

**Risk:** none. It reads output that is already there.

**What actually landed**, and one thing the plan did not ask for: the schema
step was a bare `wrangler` call, outside `apply_file`. So the one statement that
must land before any data had no busy-queue handling and no place in the meter —
a D1 still draining the previous import failed the whole run there rather than
waiting the five seconds it needed. It goes through `apply_file` now, which is
what the function's own comment always claimed ("one place that talks to D1").

---

## Phase 1 — the digest, shipped and checked, not yet acted on ✅ SHIPPED 2026-09-01

**Two corrections to this section as written, both found while building it:**

1. **One subquery per table, not two.** The plan said `verify.sql` gains a
   second scalar subquery per table. It gains nothing of the sort: the subquery
   now returns `count(*) || '|' || COALESCE(sum(row_hash), 0)`, one string
   carrying both facts. The reason is the scar this file already cites — D1
   rejected a compound SELECT at a term limit far below SQLite's documented one.
   Going from 34 subqueries to 68 is walking back toward a limit that has bitten
   here once, for nothing.
2. **`expected-counts.txt` is extended, not joined by a second file.** It is
   `table|rows|digest` now. Nothing outside `publish_cf.py` reads it — checked
   across both repos — so the format is bundle-internal and the script and the
   file always travel together.

Also shipped: `D1_REPAIR_PAUSE`, so the repair path can be tested at all. Three
rounds at the real pause is a minute of wall clock, which is why that path went
untested until the digest gave it something to catch.

**What:**

1. **`row_hash` INTEGER on every emitted table.** A 31-bit hash of the row's
   values, computed in `_emit_table` where the tuple is already being rendered.
   31 bits so that `sum(row_hash)` over 41,294 rows cannot overflow an int64
   (41,294 × 2³¹ ≈ 2⁴⁶).
2. **The bundle ships `expected-digest.txt`** (`table|count|sum`), and
   `verify.sql` gains a second scalar subquery per table:
   `(SELECT COALESCE(sum(row_hash),0) FROM "t") AS "t__h"`. Scalar subqueries in
   one row, like the counts — for the documented reason, not by preference.
3. **`find_mismatches` compares both.** Verification stops meaning "the right
   NUMBER of rows arrived" and starts meaning "the right ROWS arrived". That is
   strictly stronger, and it catches 2026-08-19 exactly as the count did.
4. **Assert the key `(id, origin_ref)` is unique per table at emit time**, and
   record the answer per table in `MANIFEST.json`. Do not fail the build on it
   yet. Record it.

**Why:** this is the entire mechanism of phase 2, landed while the load is still
a full rewrite — so it can be wrong without being dangerous.

**The thing to watch:** which tables actually report a non-unique key. ADR-0026
quotes 3,444 rows against 1,984 distinct ids, but that is the whole record;
the *served* `t1_notes` is 1,454 rows and may carry no collision at all. Phase 1
answers that from production data instead of from my inference.

**Answered, 2026-09-03 nightly (run 33754409087):** `t1_notes` 1,454 rows, **no
collision**. `t0_film` 609 rows, one. `t0_music` 41,601 rows, **32,544 share a
key**. So the guess above was right about notes and wrong about where the problem
is. The cause is in the loader and it is deliberate: `Row.__post_init__` hashes
the sorted PAYLOAD (`provenance.py:60`), a scrobble's payload is
artist/album/track, the play time is the envelope's `created`, and `origin_ref` is
the export filename — so repeat plays of one track collapse onto one key.
`csv_sources.py:100` says why the zone never wanted a key: the merge that used one
doubled the corpus (38,554 → 78,257). See phase 3, which is the part this narrows.

**Deploy note:** `row_hash` is a new column on every table, which is normally
the pin-ordering hazard. It is not one here: it reaches no answer, because all
five `SELECT *` sites in `tools.js` read CTEs with explicit projections, never a
base table. Re-check that when you add the column. It also costs no extra
writes — a column is not an index.

**Verify:** a nightly whose verify step compares digests, plus the uniqueness
report in the step summary.

**Result, 2026-09-03:** `== verifying rows and digest ==` / `every table matches
the bundle`, first attempt, no repair. So D1 and the bundle agree about content
and not only about counts — the half of 2026-08-19 a count could never see.

**What to look for on the first nightly after the pin moves:**

- `== done: warehouse now matches this bundle — N rows written ==`. N should be
  near 86,981. A number far above it means something writes that this plan has
  not accounted for.
- `== verifying rows and digest ==` passing on the first attempt. A digest
  mismatch here is not a false alarm: it means the bundle and D1 disagree about
  content, which the count could never see.
- Any `— N rows share a key with another` line in the publish step. That is the
  question ADR-0026 §1 could only guess at, answered by production.

---

## Phase 2 — skip the tables that did not change ✅ SHIPPED 2026-09-06

**What:** before writing anything, read `(count, sum)` per table using the
`verify.sql` that phase 1 already ships — one query, one round trip, taken
*before* the load. Then:

| live vs bundle | action |
|---|---|
| digest matches | skip the table entirely — zero writes |
| digest differs | reload that table in full, exactly as today |
| shape differs | reload that table in full, whatever the digest says |
| table absent | reload that table in full |

**The shape check is not optional.** Compare the bundle's DDL against one
`SELECT name, sql FROM sqlite_master WHERE type='table'`. An *added* column
changes the hash and reloads anyway; a *removed* one does not, and without this
check the stale column survives forever. That is the `first_seen` scar
(`publish_cf.py:112`) arriving from the other direction.

**What it buys, measured:** on 2026-09-01, 31 of the 34 tables matched
production exactly. The night's load would have been `t0_beer` +
`t0_criticism` + `t0_event` — about 3,819 rows written instead of 86,981.

**Why this is the safe half.** Nothing is patched or merged. A table is
rewritten byte-for-byte as it is today, or left alone because it was already
proved equal. The revocation argument is unchanged: a held row changes its
table's digest, so the table rewrites and the row is gone.

**What stays exactly as it is:** the reconcile, the shrink guard (it reads live
counts, not the digest), the read-back at the end, and the repair path — which
reapplies the FULL `data/<t>.sql`, never a diff.

**Rollback:** `EXO_D1_LOAD=full` forces the old path with no revert and no
deploy. Keep it for a fortnight after the phase lands.

**Verify:** rehearse against a scratch database first (below), then one nightly
with phase 0's meter printing the total.

### What actually landed, and three ways the section above was wrong

**1. The shape check is not a DDL comparison.** The table above says "shape
differs → reload", and the paragraph under it says to compare the bundle's DDL
against `SELECT name, sql FROM sqlite_master`. Two things are wrong with that.

It misses **indexes**, which is the serious one. An index is not a column, so a
table whose columns match keeps whatever indexes an earlier run left it — and
a74d25d, the commit that removed the `t0_music` artist index and bought the
headroom this whole plan spends, is precisely that change. Against a load that
skips matching tables it would have changed nothing in production and said
nothing about it. Nothing in this plan noticed that until the tests were written.

And it is more machinery than the job needs. What shipped instead: **a fingerprint
of the table's DDL is folded into every row's hash**, so a changed column, a
changed affinity or a changed index list all become a changed digest — and a
changed digest was already a full reload with its own `DROP`/`CREATE`. No
sqlite_master parsing, no DDL text compared across two systems, and no new query
shape put to D1, which matters given what happened to the last one.

The one case that cannot reach is a table with **no rows**: no rows, no hashes,
digest 0 whatever the shape. So `import.sh` never skips a table the bundle says is
empty. That costs nothing — re-creating an empty table writes nothing — and it
closes the hole this section was right to worry about.

**2. `schema.sql` had to be split.** Not mentioned above, and it is the change
with the widest reach. Applying `schema.sql` drops all thirty-four tables, which
is exactly what a load rewriting three must not do, so the bundle now ships
`ddl/<table>.sql` as well. `schema.sql` stays whole because
`worker/test/harness.mjs` and `worker/test/sqlcheck.mjs` both read it; the import
does not any more. Each reloading table's DDL is concatenated in front of its own
data, so the separate `== schema ==` pass is gone — and that is an improvement on
its own: a failing batch now leaves the tables it never reached untouched, where
the schema pass had already dropped every one of them.

**3. The repair reapplies the DDL too.** A table that reaches the repair after
being SKIPPED was never re-created this run, so there is no earlier state of it in
this run to patch on top of — which is ADR-0026 §3's own rule. The repair
concatenates `ddl/<t>.sql` in front of `data/<t>.sql`.

**The probe is the read-back query, asked early.** `verify.sql` unchanged, run
once before anything is written — which also satisfies the stale-read rule
literally: it is taken before the run's first byte, including before the
reconcile's `DROP`s. If it fails for any reason the run reloads everything and
says so. A brand-new database lands there by design, because a scalar subquery
over a table that does not exist fails the whole query.

**Tested against real SQLite, not a stub** (`tests/test_load_is_a_diff.py`, 19
tests). The fake `wrangler` executes the SQL against a real database and speaks
wrangler's JSON back, so the probe reads what the load actually wrote. That is the
direct answer to fact 2 below. What it proves: the same bundle twice writes zero
rows; only the changed table is rewritten; a changed row with an unchanged count
is caught; an index change reaches a table that would otherwise skip; a column
change on an EMPTY table reaches production; a held row still leaves; a revoked
table is still dropped; a partial bundle still drops nothing; the read-back still
covers skipped tables and repairs one that lost a row mid-load; and
`EXO_D1_LOAD=full` still rewrites everything.

### What to expect on the first nightly, and it is not "hundreds"

The `~3,819` figure above is real and it is also the best night this will ever
have. It comes from 2026-09-01, when the lastfm and letterboxd caches were both
unchanged — so `t0_music` did not move and 31 of 34 tables matched. **On a night
when scrobbles arrive, `t0_music` reloads in full**, because three new plays change
its digest and phase 2's unit is the table.

Worked from the 2026-09-03 delta, which is an ordinary night:

| table | rows | moved |
|---|---|---|
| `t0_music` | 41,601 | +3 |
| `t0_chat` | 10,971 | −21 |
| `t0_event` | 1,704 | +13 |
| `t0_criticism` | 180 | +15 |
| `t0_chat_topic` | 371 | −1 |
| `t0_release` | 28 | +1 |
| **reloaded** | **≈54,855** | for a delta of ~54 rows |
| skipped | ≈32,500 | |

None of those six carries an index any more, so ~54,900 rows written against
87,419 — a 37% saving and 45,000 of headroom. **Read the meter against 54,900, not
against hundreds.** A number near 87,000 means the probe is not skipping and is
worth stopping for; a number near 4,000 means it was a quiet night for scrobbles.

The two tables that dominate that bill — `t0_music` and `t0_chat` — are the two
phase 3 names, which is the argument for phase 3 arriving as a measurement rather
than as a guess. Same for the notes lane: one edited note re-derives `t2_atom`, so
a fire costs ~16,000 (7,935 rows + as many index entries) rather than the ~42,000
it costs today. Better, not free, and bucketing is what makes it free.

**Still to verify:** one nightly, read against the table above.

---

## Phase 3 — row-level diff, only where phase 2 still costs

Phase 2's meter says which tables reload often and are big. Expect `t0_music`
(41,294, grows daily), `t2_atom` (7,935), `t0_chat` (10,992). Nothing else is
worth the complexity.

**Read the phase 1 answer above before starting.** `t0_music` — the table this
phase exists for — has 32,544 of 41,601 rows sharing `(id, origin_ref)`, so it
**cannot be keyed that way**, and phase 3's `DELETE` by key would take out 78% of
the table to remove one row. Two ways out, neither decided:

- **Bucket and rewrite the bucket.** `substr(created,1,7)` is already the plan
  below, and a bucket needs no key at all — it is phase 2 one level down. A day of
  new scrobbles lands in one month bucket, and rewriting that bucket costs the
  month rather than the corpus. This is the cheap answer and it inherits phase 2's
  whole argument.
- **Put `created` in the key.** Correct for scrobbles and a change to
  `provenance.py`'s envelope/payload split, which is load-bearing for every zone.
  Do not reach for it first.

`t1_notes` is unique in the served slice, so it can be keyed. `t2_atom` has not
been measured — it printed no duplicate line, but check it rather than assume.

**What:**

- **Bucket the table.** `substr(created,1,7)` where a `created` exists — a day
  of new scrobbles lands in one month bucket, so one bucket differs and the rest
  are proved equal. `row_hash % 64` where there is no date. A hash bucket is the
  fallback and not the default, because hashing spreads a day's additions across
  every bucket, which is the opposite of what this is for.
- **Ship per-bucket digests; read them back with one `GROUP BY`.** Pull keys
  only for the buckets that differ.
- **Emit `diff/<t>.sql`** — DELETE by key, INSERT for new rows — *beside* the
  full `data/<t>.sql`, which stays as the fallback and as the repair path.
- **Only the four project tables need a true UPDATE** (2,248 rows). Their ids
  key the noun while `days_idle` and `last_commit` move under them
  (`loaders/project.py:30`). Everywhere else a changed row is a new id and a
  vanished old one, so insert + delete is the whole operation.

**Two unknowns to measure rather than assume:**

1. How many rows `wrangler d1 execute --json --command` can return before D1 or
   the CLI truncates. Bucketing exists to keep this small, but find the ceiling
   deliberately against a scratch table — not at 3am against production.
2. Whether `t2_atom` churns wholesale when the atomiser changes. If it does, its
   fallback is a full reload and that is a fine answer.

---

## Phase 4 — the notes lane ✅ FALLS OUT OF PHASE 2

No new code, exactly as written: the partial bundle takes the same path, so a fire
where one note moved costs near zero instead of ~42,000. Covered by
`test_a_partial_import_still_loads_its_own_table` and by
`test_a_partial_bundle_takes_the_same_path_and_drops_nothing`, which also holds
the line that matters more — a scoped run still reconciles against nothing.

**Still to verify in production:** edit one note in Notion and read the meter on
the lane it triggers.

---

## Rehearsal, before phase 2 touches production

A scratch database (`warehouse-rehearsal`) and a few small tables. **The trap:**
a rehearsal load spends the same account's daily write budget as production, so
rehearse with small tables, or after the paid plan, or on a day the nightly has
already run.

*What was done instead, and why it is better here.* The rehearsal that shipped is
`tests/test_load_is_a_diff.py`: a fake `wrangler` that executes the bundle's SQL
against a **real SQLite file** and answers in wrangler's JSON. D1 is SQLite, so the
probe reads back what the load actually wrote rather than what a stub was told to
say — which is the whole of fact 2 above — and it costs no write budget, so it runs
on every commit instead of once. It cannot cover the two things that are D1 and not
SQLite: the async `--file` import queue, and the stale read. Both are handled by
code that production has already proved (the busy-queue handler, and the
three-round repair), and neither is what phase 2 changed. The nightly remains the
last word.

---

## Sequencing and size

| phase | size | ships alone? |
|---|---|---|
| 0 — the meter | an hour | ✅ shipped 2026-09-01 |
| 1 — the digest | a day | ✅ shipped 2026-09-01 |
| 2 — skip unchanged tables | the real one | ✅ shipped 2026-09-06 |
| 3 — row-level diff | only what the meter justifies | not started, and narrowed — see above |
| 4 — the notes lane | free | ✅ falls out of 2 |

## What would make me stop, and where it did not matter

If phase 1 reports several tables with a non-unique key, phase 3 gets harder for
those tables — bucket by bucket, they fall back to a full reload. **Phase 2 is
unaffected: it never uses the key.** That is the reason for this order. The
cheap phase does not depend on the answer to the risky question.

*This is the part of the plan that paid.* Phase 1 reported one table with a
non-unique key and it is the one phase 3 was for. Phase 2 shipped anyway, on the
same day, because it never asked the question — and phase 2 is most of the win.
The order was the decision that mattered.
