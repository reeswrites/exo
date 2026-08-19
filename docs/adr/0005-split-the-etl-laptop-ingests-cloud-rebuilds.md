# ADR-0005 — Split the ETL: laptop ingests, cloud rebuilds

Status: proposed · 2026-08-18

## Context

The warehouse is about to grow a remote read surface — an MCP server a hosted
assistant can call — which means the store has to exist somewhere other than
this laptop, and has to be no more than about a day stale. ADR-0001 assumed
"local-first, single-user, no server". That constraint is now lifted for
**reads**, not for ownership.

Two facts decide where ETL can run:

1. **One T0 source is genuinely machine-bound.** `applenotes/extract.py` reads
   the local macOS NoteStore SQLite; Apple Health is a phone export. No cloud
   runner can produce those.

   *Corrected 2026-08-18:* this ADR originally claimed Goodreads, Letterboxd and
   Untappd had no pullable API and required a browser Export click. That is
   false. The blog repo already implements RSS fetchers for all three
   (`goodreads.com/review/list_rss/{id}`, `letterboxd.com/{user}/rss/`
   incremental, `untappd.com/rss/user/{user}?key=`). They are network-pullable
   and cron-able. What is actually true is narrower: **the warehouse loaders read
   the dated CSV exports rather than the RSS path**, so the store lags what the
   producer can already fetch — the warehouse film edge sat at 2026-03-25 while
   the blog's RSS-derived `movies.json` had been written 2026-08-10. That is a
   wiring gap, not a source limitation.
2. **Everything else is portable, but not free.** `wh rebuild` parses
   CSV/JSON/markdown and writes parquet, and T2 derive runs **bge-small over
   every atom and note** (`t2/atom_vec`, `t2/note_vec`). `loaders/embeddings.py`
   reads *precomputed* vectors, but that covers media embeddings only — there is
   real inference in the pipeline. It is CPU-portable and cached, so it runs
   anywhere, but the runner has to be sized for it.

## Decision

Split by what the source **requires**, not by tier:

| Runs where | Does what |
|---|---|
| Laptop (launchd) | macOS-bound and manual-export ingest; syncs `raw/` to S3; fires the cloud rebuild |
| GitHub Actions | checks out warehouse + second-brain, pulls `raw/`, runs `wh rebuild` then `wh verify`, publishes `zones/` + catalog |

GitHub Actions is the cloud runner: the private-repo free tier (2000 min/mo)
against a job measured 2026-08-18 at **7m02s cold** (`sync-raw` 1s, `rebuild`
6m19s, `verify` 42s) and **72s warm**. The gap is entirely embedding: cold pays
the bge-small download from HF Hub plus a full pass over 10.5k atoms and 3.4k
notes; warm hits the content-addressed cache at `zones/_cache/embed.parquet`
(65MB) and only embeds what changed.

A fresh runner is cold **every** run, so the cloud leg must restore two caches
or pay 7 minutes nightly: `zones/_cache/embed.parquet` and the HuggingFace model
dir. Even uncached — 210 min/mo, about a tenth of the allowance — this fits; the
cache is a courtesy, not a requirement. Set `HF_TOKEN` regardless: the cold run
warned that unauthenticated Hub requests are rate-limited, which is a flaky-CI
failure waiting to happen. The deploy path needs the same secrets and OIDC role. `wh verify` gates publication — the anti-collapse check of ADR-0001
becomes a release gate rather than a thing run by hand.

**Rejected.** *Glue*: Spark billing and a Data Catalog for a 40k-row job whose
catalog is already DuckDB. *Lambda for ETL*: container packaging to move from
free to free. *Cloudflare Workers*: Python Workers are Pyodide, no native
DuckDB, and 128MB will not hold the T2 vectors. R2 in place of S3 stays open —
zero egress — but at 82MB the difference is cents.

## Data never enters git

`.gitignore` already excludes every `zones/*.parquet`, the catalog, and `raw/`.
That stays: code to git, data to S3.

the exports directory currently holds `google-service-account.json` and
`trakt-tokens.json` — live credentials. They move to a secret store before any
sync exists; a bucket of exports is not a place for service-account keys.
Actions authenticates to AWS by OIDC role assumption, not stored access keys.

## Freshness contract

Target: no zone older than roughly 24h.

- A **LaunchAgent** with `StartCalendarInterval`, not a crontab. launchd runs a
  missed calendar job when the machine wakes; cron silently drops it. That one
  property is the whole reason for the choice.
- `pmset repeat wake` a few minutes ahead of it, so "asleep" does not mean
  "skipped". Reliable on AC power; a lid-shut Apple Silicon machine on battery
  sleeps deeply enough that the wake may not fire.
- Actions runs its own daily schedule regardless. If the laptop stays shut for a
  week, T1 and T2 keep refreshing from second-brain; only T0 goes stale.

The agent runs a **compiled launcher**, not the script directly. `~/Documents`
is TCC-protected and launchd-spawned processes inherit none of the Terminal's
grants — a LaunchAgent pointed at a script in there fails to read it at all
(exit 127; verified by probe 2026-08-18). Full Disk Access has to be granted to
*something*, and granting it to `/bin/zsh` would privilege every shell script on
the machine. Instead `scripts/wh-daily-launcher.c` builds to
`~/.local/libexec/warehouse/wh-daily`, holds the grant alone, hardcodes the
script path (an argv-taking launcher would be a general-purpose way to run
anything with FDA), and fork+execs rather than exec'ing so it remains the
TCC-responsible parent. Rebuilding it changes the code hash and invalidates the
grant — it has to be re-added by hand. Apple Notes ingest will need this grant
regardless, so the launcher is the narrow way to pay a cost that was coming.

Staleness is **recorded, not assumed**: each leg stamps its completion and the
read surface exposes those stamps, so a consumer can say "T0 is four days old"
instead of quietly serving old rows as if they were current.

## Consequences

- T0 freshness is gated on **wiring**, not on a human clicking Export. Pointing
  the consumption loaders at the existing RSS/API fetchers (Goodreads,
  Letterboxd, Untappd, plus Last.fm and Trakt, whose tokens are already on disk)
  would move nearly all of T0 into the cloud leg and leave the laptop
  responsible only for Apple Notes and Apple Health.
- The laptop stops being the only machine that can rebuild, but remains the only
  one that can ingest Apple Notes.
- Running cost is approximately zero: free Actions minutes, cents of S3.
- ADR-0001's "no server" holds for writes. Reads get one, and it is a lens over
  published artifacts — same posture as the catalog itself.

## What may leave: the serve projection

`wh publish` builds `zones/_serve/` from `serve-manifest.json`, and only those
bytes ever reach the host. The filter is physical, not a `WHERE` clause in a
tool: a query-time filter is a promise, and an injected prompt or a tool bug can
break a promise. Held rows are simply not on the box.

The manifest is **fail-closed** — every zone and every note folder must carry an
explicit `serve`/`hold`, and an unlisted one fails the build. A new
`Therapy Notes/` folder cannot be published by being forgotten. Held today:
`t0_chat` (10k turns of assistant conversation), the `rows` union view (it would
bypass every per-zone decision), all `cache_*`, and the note folders
`Personal Reflection`, `Journaling`, `Poems`, `Archive`, and unfiled.

Holding a folder has to **propagate**, because `t2_atom` carries note text
verbatim and the vector tables resolve back to titles. `origin_ref` is the join
key — `t1_notes.id` is not unique (3,444 rows, 1,984 distinct ids; two ids cover
1,462 rows) — and refs are published as `sha256[:16]`, since a filename like
`2022-04-15-relationship-standards.md` states its subject with no body attached.

**Two axes, either of which vetoes.** Folder alone decides nothing. The vault's
`CONTEXT.md` promises that `refined/unshared` "never leaves"; under a folder-only
manifest that promise held *by coincidence* — those notes are unfiled, and
unfiled happens to be held. Unfiled is a plausible thing to widen (it also holds
the chat-promoted notes), and widening it would have published private prose
silently. So a note now publishes only if its folder says serve **and** its
gradient zone (`path_zones`) says serve. Verified adversarially: with unfiled and
`Archive` both flipped to serve, all five `refined/*` and `raw/daily/*` notes
stayed held. Folder is where a note was filed; path is what it *is*.

**Folder is not sufficient.** An audit of the first projection found held
passages present under served notes: the vault versions and copies across
folders (`…ambient-music.md` held, `…ambient-music-v2.md` in `Projects`;
`refined/unshared/asexual-spectrum-desire.md` reproduced inside a `Drafts`
note). Folder is where a note was filed, not where its content went. So publish
also runs a **content guard** — any served note or atom with ≥30% 12-word
shingle overlap against any held body is dropped. It removes a further 15 notes
and 110 atoms. Without it the folder filter leaks exactly the material it exists
to protect.

## Serving it: Cloudflare, not AWS

The serve layer's hardest requirement is not SQL, it is embedding the *query*
into the same bge-small 384d space as the stored vectors. On Lambda that means
shipping the model inside the container (300-500MB, cold-start seconds); on
Workers it is a Workers AI binding. That decides the host.

`wh publish --cf` reshapes the projection into `zones/_serve/cf/`: per-table
SQL for D1 (53,586 rows, a 20MB SQLite file, verified to load and to use its
indexes) and a flat float32 blob for R2. The vectors are already unit-norm, so
cosine is a dot product, and 10,971 x 384d x 4B = 16.85MB loads whole into a
Worker isolate — brute force replaces a vector database. That is deliberate:
Vectorize's free tier is ~5M stored dimensions and the corpus is at 4.21M
already.

Nothing here re-decides policy; it reads what `wh publish` already filtered, so
a held row cannot reappear by taking a different road out.

**The bundle is authoritative over D1, not additive.** Row-level tightening
already revoked — every data file opens with `DELETE FROM`, so holding a folder
or a path zone removes those rows on the next import. Table-level tightening did
not: a zone flipped to `hold` simply stopped being emitted, and D1 would keep the
table and every row in it while the manifest claimed it held — failing in the
quiet direction, at exactly the moment someone is tightening policy because
something worried them. So the bundle ships `served-tables.txt` and an
`import.sh` that drops anything in D1 not on that list, including tables left by
an older manifest or made by hand. D1's own bookkeeping (`sqlite_*`, `_cf_*`,
`d1_*`) is protected, or the first import destroys what it is loading into.

**The join key trap, again.** The first `--cf` build hung: joining note vectors
to notes on `id` amplified 3,064 rows into 1,087,978, each carrying 384 floats.
The cause is not a hash collision — the vault holds **742 versioned copies of one
note and 732 of another** (`...-v10.md`, `...-v100.md`, `...-v101.md`), all
correctly sharing a frontmatter id. 1,474 of 3,444 notes are versions of two
notes. The emit now dedupes before joining and asserts the row count is
unchanged, so this class of bug fails loudly instead of writing a 1.6GB blob.

## Open

Whether the vault's 1,474 versioned duplicates should be collapsed — serving 742
near-identical copies of one note would make note-level search return the same
note 742 times.

Whether `Archive` and the unfiled notes stay held, and whether the 15
guard-dropped notes should be reviewed individually rather than dropped
wholesale.
