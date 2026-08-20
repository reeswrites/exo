# ADR-0018 — The notes tree is the original; the bucket is a mirror

Status: accepted · 2026-08-20

## Context

ADR-0017 made `notes/` the landing tree for every note source. That changed what
the directory *is*.

Everything else an instance ignores has an upstream. `raw/` mirrors somebody
else's directory and `exo sync-raw` refills it. `zones/` and `catalog/` rebuild
from `exo rebuild`. `backups/` are copies of those. `notes/` is now the one that
does not: once an Apple Notes database has been decoded or a Notion export
unpacked, that tree is the only original. The silo it came out of may be gone,
may have lost the note, or may simply be a product you no longer pay for — which
is the situation this whole system exists because of.

And it was gitignored. `exo backup` did not help: it copies `zones/` and the
catalog, skips `notes/` entirely, and writes into `backups/` **on the same
disk**, which is a snapshot rather than a backup — it survives a bad rebuild and
nothing else.

Meanwhile the deployment already has object storage in it. `publish --cf` writes
`vectors.f32`, `vectors.json` and `brief.md` into an R2 bucket, and the Worker
reads them from there. The bucket exists, the credentials exist, the upload step
exists. The open question was whether the *record* should live in it — object
storage with versioning turned on has an obvious appeal as a backup you get for
free by keeping things in sync.

## Decision

**1. `notes/` is committed.** It is markdown. Git holds it for nothing, diffs it
usefully, and gives point-in-time restore of the whole tree with `git checkout` —
which is exactly the operation a backup is for and exactly the one object
versioning is worst at. New instances get this from `exo init`; an existing one
removes the `/notes/` line from its `.gitignore`.

**2. Object storage is a mirror, one-directional, and never read back as truth.**
Disk is the record. The bucket receives it. Nothing in the ingest, index, derive
or publish path ever reads a note back out of a bucket.

This is not caution about object storage; it is the write-scope model. T1 is the
tier written *by you, by hand* — that is what `grounds=True` claims about every
row in it, and it is the claim derivation relies on. A bidirectional sync makes
the bucket a writer, and a bucket is a thing anyone holding a key can write to.
"Keep it in sync" is one word doing two jobs: mirroring out is a backup,
mirroring back is a new, unauthenticated author of your authored tier.

**3. R2 rather than S3**, for this deployment specifically: no egress fees, the
account is already in the stack, the Worker can read the same bucket, and the
vector bucket is already bound. The mirror is one key per file, so any S3-shaped
store works — nothing here depends on Cloudflare beyond the bill.

**4. Versioning is a safety net, not a restore plan.** Object versioning is
per-key: "the tree as of last Tuesday" means enumerating every key, resolving
each to the version live at that timestamp, and writing them all back. That is a
script, and an untested script is a promise rather than a backup. Turn versioning
on — it is the thing that survives a bad sync deleting half the tree, and it
costs nothing — but the restore path that gets *tested* is git, and the case
versioning covers is the one git cannot: the machine is gone.

**5. Corpus embedding stays on the ingest leg.** Three reasons, and the second is
the one that decides it.

*It is already incremental.* Vectors key on `sha256(text)` (`exo/embed.py`), so a
new Notion page costs one embed and a re-import of an unchanged corpus costs
zero. The nightly is already a poll in the only sense that matters.

*The two bge-smalls are not the same model.* Measured 2026-08-19
(`worker/README.md`): the same text through Python fastembed and through Workers
AI gives cosine **0.9031**, not ~1.0. That is fine where it currently sits,
because only the *query* goes through Workers AI and a systematic offset applied
to every comparison leaves the ranking intact. It is not fine for a corpus.
`vectors.f32` is one flat row-major blob compared by brute-force dot product,
with no field saying which implementation produced which row — so an embedder
that vectorises *some* notes in the cloud puts two subspaces in one blob, and
every cross-subspace comparison is depressed by an amount nothing can see or
correct. A cloud-side corpus embedder is all-or-nothing.

*The wall wants it home.* `exo verify` proves T2 regenerates identically from
T0+T1. A vector minted somewhere else and never returned is a T2 row the
determinism proof cannot reach.

**What a poll would actually buy is freshness between nightlies**, and the cheap
version of that is running the ingest leg more often. If it is worth wiring, the
Cloudflare shape is R2 event notifications → a Queue → a consumer that re-runs
the *bundle* step against a record that was embedded at home. Not a second
embedder.

## Consequences

- The restore path is: clone the instance repo, `export EXO_HOME`, `exo rebuild`.
  Notes and items come back from git; zones, catalog and vectors regenerate; the
  bucket is not needed. That is the path to test.
- The mirror is a step in the nightly, beside the existing R2 uploads, and it is
  `--delete`-free: a note removed locally stays in the bucket until versioning
  ages it out. A sync that can delete is a sync that can delete everything.
- An instance repo now grows with every note. Markdown is small and git is good
  at it; a decade of notes is smaller than one Last.fm export.
- The isolate has a ceiling. `vectors.f32` is loaded whole into the Worker —
  10,971 × 384 × 4B = 16.85MB today, against a 128MB isolate — so a large silo
  import is the first thing that will approach it. At 1,536 bytes per vector the
  practical ceiling is tens of thousands of atoms, not millions. When that
  arrives the answer is to narrow what is published, not to add a vector
  database: the corpus that matters is the served one.
- The door left open, deliberately: flipping *every* corpus vector to Workers AI
  is a coherent alternative — embed-on-arrival, no local model, no nightly. It
  costs a full re-embed of the corpus, makes a rebuild need the network, and ends
  the offline guarantee. It is not taken here, and it is not foreclosed.
