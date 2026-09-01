# ADR-0026 — The bundle stays authoritative; the load becomes a diff

Status: proposed · 2026-09-01

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
time, per table**: the emitter asserts the key is unique over the rows it is
about to write, and any table that fails takes the full-reload path instead. A
key assumed to be unique is the kind of default that is eventually wrong in
production and silent about it.

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

Prefer the previous bundle: it costs no D1 quota and it is already carried.
Fall back to reading the keys when it is missing — a first run, a restored
account, a lane whose predecessor failed.

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

- A normal night writes hundreds of rows instead of ~128,275. The nightly sits
  two orders of magnitude inside the free tier, and the caller log keeps
  recording.
- The notes lane stops costing ~42,000 rows written per fire, so a day of Notion
  edits stops being a budget event. This is the larger win of the two: the
  nightly is bounded at one run, and the notes lane is not.
- The D1 tables gain a unique key, and a unique index is itself an index — one
  more write per inserted row. That is nothing on a 200-row night and it makes
  the full reload dearer (~+74,000). The full reload is now the exception, so
  this is the right way round.
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
- **The `t0_music.artist` index.** It buys 41,294 writes a night and cannot
  serve the queries that exist: `music` filters on `lower(artist) LIKE '%…%'`
  (`tools.js:635`) and `releases` groups on `lower(artist)` (`tools.js:1352`),
  neither of which an index on `artist` can answer. Removing it is a one-line
  change to `_INDEXES` and needs no ADR — but until the load is a diff, it is
  also the difference between ~128k and ~87k writes a night, which is the
  difference between over the free limit and under it.
