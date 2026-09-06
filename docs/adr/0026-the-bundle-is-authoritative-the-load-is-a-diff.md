# ADR-0026 — The bundle stays authoritative; the load becomes a diff

Status: accepted · 2026-09-01 · phases 0–2 shipped 2026-09-06

Extends [ADR-0005](0005-split-the-etl-laptop-ingests-cloud-rebuilds.md). It
changes how "the bundle is authoritative over D1" is *enforced*, not whether.

## Context

`exo publish --cf` emits `DROP TABLE` + `CREATE TABLE` + `INSERT` for every
served table (`publish_cf.py:117`), and `import.sh` applies the lot. The
database is not updated nightly; it is rebuilt nightly. That was the right first
shape — it makes the bundle authoritative over the *schema* as well as the rows,
by brute force — and it has now outgrown the meter it runs on.

### One rebuild is larger than a day's write budget

From the load in nightly run 33509858427 (2026-09-01, 12:57:27–12:58:07 UTC),
counted off the guard step that precedes it:

| | rows |
|---|---|
| Rows in the 34 tables that reach D1 | 73,844 |
| Index entries, which D1 bills as rows written | 54,431 |
| **One full load** | **≈ 128,275** |
| D1 free tier | 100,000 / day |

The index column is not overhead anybody chose to pay: D1 charges one row
written per index entry, and `_INDEXES` (`publish_cf.py:54`) declares seven of
them. `t0_music.artist` is 41,294 of the 54,431 by itself, so the scrobble table
costs 82,588 writes a night — 64% of the bill — for 41,294 rows.

The schema half is cheap and can stay: that same run reports 77 queries, 3,692
rows read and **120 rows written** for the whole `DROP`/`CREATE` pass. Dropping
a table is not billed per row it held. Only the re-insert is.

### The night wrote 128,275 rows to change about sixty

The guard prints production against the new bundle, table by table. On
2026-09-01 the whole delta was:

    t0_beer          1965 -> 1967      +2
    t0_criticism      140 ->  151     +11
    t0_event         1653 -> 1701     +48

Every other table matched, and the run's own log records the lastfm and
letterboxd caches as unchanged. More than 99% of the writes re-wrote rows that
had not moved.

### What exhausting the budget actually breaks

Reads are unaffected — they have their own budget, and the surface only reads.
Two things do break, and they break unequally:

- **The caller log goes quiet without saying so.** `wh_audit` and `wh_callers`
  are the surface's only writes, and the writer is wrapped in a `try`/`catch` so
  it can never take the surface down. `migrations/0001-caller-observability.sql`
  warns about this exact property: a failed audit write is silent. Once the
  budget is spent, "by whom" stops being recorded for the rest of the UTC day
  and nothing announces it.
- **A second lane on the same day fails its import.** That one is loud, because
  of the read-back added after 2026-08-19. This is the honest half of the
  failure mode.

The notes lane is the reason this is not a once-a-day problem. It fires on a
Notion edit, not on a clock, and its partial bundle re-writes `t1_notes`,
`t1_open_thread`, `t2_atom` and `t1_post` in full: 10,193 rows and 10,843 index
entries, deleted and then inserted, ≈42,000 rows written per fire. Two edits in
a day plus the nightly is three times the budget.

Moving the account to the paid plan raises the ceiling and should be done
regardless. It does not make a 128,275-row rewrite the right shape for a
sixty-row delta, and it does not make the notes lane cheap.

## Decision

**The load stops being a rewrite and becomes a diff: insert what is new, delete
what left, update the few rows that mutate in place. The bundle stays
authoritative, and the loader now has to *prove* that rather than assert it by
demolition.**

### 1. The key is `(id, origin_ref)`, and it is checked, never assumed

Every row already carries a stable `id` — first column of the envelope
(`provenance.py:23`), content-derived. What it does not carry is a guarantee of
uniqueness, and one served zone breaks it today: `publish.py:20` records
`t1_notes` at 3,444 rows against 1,984 distinct ids, two ids covering 1,462 rows
between them, because `Row.__post_init__` hashes the sorted payload values
(`provenance.py:62`) and two identical notes therefore hash alike. `t2_atom` has
the same shape of exposure — its id is `_hash(span, ref)` (`t2.py:176`), so a
line repeated inside one note collides with itself.

So the key is `(id, origin_ref)`, which is what `publish` already treats as the
join key throughout for exactly this reason. And it is **verified at publish
time, per table**: the emitter counts the rows that share a key and records the
answer in `MANIFEST.json`. A key assumed to be unique is the kind of default that
is eventually wrong in production and silent about it.

*Answered by production 2026-09-03, and the answer is not the one above.* The
first nightly carrying the check reported:

| table | rows | share a key |
|---|---|---|
| `t1_notes` | 1,454 | **none** |
| `t0_film` | 609 | 1 |
| `t0_music` | 41,601 | **32,544** |

So the inference in the paragraph above is wrong where it is specific.
`publish.py`'s 3,444 rows against 1,984 distinct ids describes the whole record;
the *served* notes slice is a different, smaller set of rows and its key is
unique. The collision is `t0_music`, at 78% of the table, and the cause is not a
defect: `Row.__post_init__` hashes the sorted PAYLOAD values
(`provenance.py:60`), a scrobble's payload is artist/album/track, and the play
time lives in the envelope as `created`. Every repeat play of one track therefore
lands on one id — and `origin_ref` is the export filename, shared by the lot.
`csv_sources.py:100` records why the zone was built that way: keying the merge
doubled the corpus once (38,554 → 78,257), so the stream splices on time instead.

**What that changes.** Nothing in phases 0–2 — a skip never uses a key. Phase 3
cannot key `t0_music` on `(id, origin_ref)`, which is a real narrowing, because
`t0_music` is the table phase 3 exists for. Its options are a bucketed digest
with a whole-bucket rewrite, or `created` in the key. Neither is decided here.

The vector tables are not affected. They do not reach D1 at all
(`publish_cf.py:598`).

### 2. The diff is the saving; the upsert is not

D1 bills a row written for every row an `INSERT` or `UPDATE` touches, so
`INSERT … ON CONFLICT DO UPDATE` over all 73,844 rows costs what the rewrite
costs. Only sending fewer rows sends fewer rows.

The diff needs a base — what D1 currently holds — and there are two ways to get
one:

- **Read the keys back.** ~74,000 rows read against a 5,000,000/day read budget,
  and `guard-publication.sh` already queries live D1 once per table.
- **Diff against the previous bundle**, which the nightly already writes to R2
  as the warm copy.

*Corrected 2026-09-01, while planning the phases:* this ADR first said to prefer
the previous bundle, because it costs no D1 quota. That is the wrong preference.
The previous bundle records what the last run *meant* to load; D1 records what it
holds — and those differ after exactly the failures this has already had (a
batch lost in silence, a run that died between tables). Reading it back is also
free of new durable state, which the ledger's frozen-key episode is a standing
argument for. So: **read the base from D1**, cheaply, as a per-table digest
rather than 74,000 keys. See
[the plan](../plans/0026-the-load-becomes-a-diff.md) for the shape of it.

One constraint that read imposes, and it is not optional: **D1 reads back stale
for seconds after a bulk load** — on 2026-08-19 six tables verified as EMPTY and
five of them had been full the whole time. So the read that computes a diff must
be taken before the run writes anything, never between two writes.

Most zones then need no `UPDATE` at all. Their ids are content-derived, so a
changed row is a *new* id and a vanished old one: insert, delete, done. A true
upsert is only needed where the id keys the noun and the payload moves under it
— the project zones (`loaders/project.py:30`), whose `days_idle` and
`last_commit` change nightly by construction. That is 2,248 rows across four
tables.

### 3. Shape drift falls back to the full reload

`DROP`/`CREATE` is what makes a column change safe, and the scar is already in
the file: adding `first_seen` to T0 made every insert fail with "no column named
first_seen" against a database that reported itself healthy
(`publish_cf.py:112`). A diff loader cannot inherit that hazard.

So before diffing a table, compare its live columns against the bundle's DDL.
Any difference, and that table takes the old path for that run. The same applies
to a table that is absent, and to one whose live count disagrees with the base
the diff was computed from — drifted state is repaired by rewriting, not by
patching on top of an unknown.

This is per table, not per run. One new column on one zone must not cost the
other thirty-three a full rewrite.

*Corrected 2026-09-06, by building it.* Comparing live columns against the DDL is
the wrong mechanism, in two directions.

It is too weak, because it says nothing about **indexes** — and an index is not a
column, so a table whose columns match keeps whatever indexes an earlier run left
it. That is not hypothetical: a74d25d removed the `t0_music` artist index and is
the entire reason there was headroom to spend on this work. Against a load that
skips matching tables, that commit would have changed nothing in production and
said nothing about it.

It is also more machinery than the digest needs. `row_hash` already covers every
value in the row, so the fix is to **fold a fingerprint of the table's DDL into
every row's hash**. A changed column, a changed affinity or a changed index list
all become a changed digest, and a changed digest is already a full reload with
its own `DROP`/`CREATE`. No sqlite_master parsing, no DDL text compared across
two systems, and no new query shape against D1 — which matters, because the one
new query shape this file tried before was rejected outright.

One case that cannot reach: a table with **no rows** has no hashes to carry the
fingerprint, so its digest is 0 whatever shape it has. `import.sh` handles that
directly — it never skips a table the bundle says is empty. The cost is nil,
because re-creating an empty table writes no rows.

And two limits worth stating rather than discovering.

The digest proves the rows this bundle wrote are the rows that are there. It
cannot see an edit made to a data column that leaves `row_hash` alone, because
`row_hash` is stored. That is acceptable for a stated reason — the only other
writer to this database is the worker, and the worker writes `wh_*` and nothing
else.

And a sum is not a hash of the table. It is deliberately blind to ORDER, which is
right: a SQLite table is an unordered set of rows, and two loads of the same rows
must compare equal. It is also, in principle, forgeable — some other multiset of
31-bit hashes sums the same. For a change nobody is choosing, the chance of
landing back on the old sum is about 2⁻³¹, and the read-back's job is to catch
accident rather than intent.

### 4. Revocation still has to be provable

This is the part that deserves the caution, because it is a privacy property and
not a tidiness one. Today, "a held row is gone from D1" is true by demolition:
the table is dropped and only served rows come back. A diff has to *establish*
that instead, so the guarantees do not move:

- The table-level reconcile against `served-tables.txt` is unchanged. A zone
  flipped to `hold` still loses its table.
- Row-level removal becomes an explicit delete-by-key list, emitted by the same
  code that decides what may leave — never inferred by the loader.
- The `expected-counts.txt` read-back stays, and is now the check that the diff
  was *complete* rather than merely applied. It already exists for a stronger
  reason (2026-08-19, five green batches and three empty tables), and it is what
  makes this change safe to ship.
- A full reload runs weekly and on demand, as the backstop. The expensive path
  becoming rare is the point; it becoming unreachable is not.

## Consequences

- A normal night writes far less than ~128,275. Phase 2's unit is the TABLE, so
  the size of the saving is the size of the tables that did not move — and that
  varies more than "hundreds of rows" suggests. A night with no new scrobbles is
  ~3,800; an ordinary one is ~54,900, because three new plays reload all 41,601
  rows of `t0_music`. Both are inside the free tier with room for a lane fire on
  top, which the old 87,419 was not. Getting from 54,900 to hundreds is phase 3,
  and the two tables it would have to fix are the two it already names.
- The notes lane stops costing ~42,000 rows written per fire, so a day of Notion
  edits stops being a budget event. This is the larger win of the two: the
  nightly is bounded at one run, and the notes lane is not. It needs no code of
  its own: a partial bundle takes the same path.
- **Phase 2 adds no index and no key to D1.** The saving comes from not writing
  tables that already agree, which needs neither. The unique index this ADR
  costed below belongs to phase 3, and phase 3 is optional.
- The bundle grows a `ddl/` directory — the same DDL as `schema.sql`, split per
  table, because applying `schema.sql` drops all thirty-four and a load that
  rewrites three must not. `schema.sql` stays whole: the worker's test harnesses
  read it, and it is the readable record of the shape. The import no longer does.
- The DDL now travels immediately in front of its own table's data rather than in
  a pass of its own, which is better than what it replaces: a batch that fails
  leaves the tables it did not reach untouched, where a separate schema pass had
  already dropped every one of them.
- The loader gets harder to write and much harder to test, and the failure it
  can produce is a D1 that quietly disagrees with the bundle. That is the
  2026-08-19 failure, and the defence against it is already built. Do not ship
  the diff path without the read-back, and do not weaken the read-back to make
  the diff path pass.
- `import.sh` keeps one code path that talks to D1, one busy-queue handler and
  one wall-clock budget. The diff changes what is in the files, not who applies
  them.

## What this does not decide

- **Whether scrobbles belong in D1 row by row.** 41,294 of the 73,844 rows are
  one table, and the tools mostly aggregate it. That is a bigger question than
  the load mechanism and it is not answered here.
- **The `t0_music.artist` index.** ~~It buys 41,294 writes a night~~ *Done in
  a74d25d, before any of the phases: removing it took the nightly from ~128,275
  to 87,419 measured, which is under the free limit and was the point.* One
  correction to how it was argued here, because the original claim was too
  strong: five of the seven query sites can never use that index, but `music`'s
  `GROUP BY artist` and `around_the_time`'s could have used it to avoid a sort.
  It was removed for the meter and not because it served nothing — it bought at
  most a sort, inside a scan those queries do anyway, for a third of the daily
  write budget.
