# ADR-0011 — His repos are a source, and they are read through a snapshot

Status: accepted · 2026-08-19

## Context

The store had every pile of his life except the one he spends the most hours in.
Notes, scrobbles, ratings, films, saves, events, meals, conversations — all
ingested; the 44 repos under `~/Documents` were not. A reader could quote a note
*about* alchemy and not know whether alchemy had been touched since spring, or
that `hiatus/` holds twenty finished projects, or that a decision it was being
asked to re-litigate had an ADR settling it two months ago.

The gap showed up as a specific failure rather than a missing feature. Asked what
he was working on, the surface answered from his notes — the laggiest record of
the work there is, because a note requires a deliberate act of capture and a
commit does not. The freshest, most honest log of his attention was sitting in 44
`.git` directories that nothing read.

Three things had to be decided: what a repo *is* in the tier model, where the
reading happens, and whether any of it leaves the machine.

## Decision

**1. Repos are T1, not T0.** A commit subject, a README, an ADR and a TODO marker
are his words about his own work — authored, not recorded about him by an outside
system. `author=human`, `grounds=true`, `regenerable=false`, exactly like the
vault's projection. The rule that T1 is a *rebuilt projection of a live record he
owns, never a second original* holds here without amendment: the repo stays the
source of truth and the warehouse never writes to it.

**2. Four zones, because the question is four questions.**

| zone | answers |
|---|---|
| `t1_project` | what exists, where it points, how alive it is |
| `t1_project_commit` | what he worked on, dated |
| `t1_project_doc` | README / CONTEXT / ADR / plan prose |
| `t1_project_open` | markers, unchecked plan items, uncommitted files |

One flat zone would have forced every question through a shape fitting none of
them: an inventory row and a commit row have almost no columns in common, and
folding docs in would have made the repo list unqueryable without a body filter.

**3. The git reading happens on the laptop, into `raw/`, not in the loader.**
This is the load-bearing decision. ADR-0005 splits the ETL: the laptop holds the
checkouts, the cloud leg rebuilds from `raw/` and has none of them. A loader that
walked `.git` directly would therefore build four populated zones on the laptop
and four empty ones in CI — and the cloud publish would then overwrite the served
copy with nothing, silently, exactly the way a truncated fetch used to. So
`wh scan-projects` writes `raw/projects.json` on the machine that has the repos,
and the loader reads only that snapshot. It refuses to overwrite a good snapshot
with an empty one, for the same reason the event fetchers refuse: a scan that
finds nothing must not be why the surface forgets he builds things.

**4. Prose and metadata are published; source code is never captured at all.**
Not filtered at publish — never read. What crosses is repo metadata, commit
subjects, README/CONTEXT/ADR/plan bodies capped at 20K chars, and TODO text with
its file path. The reasoning matches ADR-0008's on allergies: the value is in a
remote reader being able to reason about the work, and metadata about a project
is not the project.

**5. Scope is `~/Documents`, two levels deep, minus a denylist.** Two levels
because `hiatus/` is where most of the finished work lives, and a depth-1 scan
would have seen an empty folder. Filing is not a privacy boundary here — the note
folder and path axes of `serve-manifest.json` describe the vault and have no
meaning for a repo — so **a repo that must not leave has to be excluded at scan
time** via `WH_PROJECTS_DENY`. That is the only lever, and it is stated here
because assuming otherwise is the mistake this note exists to prevent.

## Consequences

The brief now leads with what he is building, ranked by commit volume in the last
90 days rather than by last-commit date: nearly half these repos were `git
init`-ed the same week, so recency alone floats a one-commit import above a
project he has pushed on for months.

`MAX_BYTES` for the brief moves 6000 → 7000. The pile does not compress into the
slack that was left, and truncating the freshness stamp to protect the old number
would have traded a stated limit for a hidden one.

The four new tools are the fourth cluster on the read surface after notes,
consumption and events. They inherit ADR-0007's caps unchanged.

Staleness is now two-legged. The laptop rescans nightly before the rebuild, so
the laptop-published copy is a day old at worst. The cloud leg sees only what
`push-raw.sh` last uploaded, so a stale tarball means the cloud publishes stale —
not empty — project zones. The shrink guard covers the empty case; nothing covers
the stale case except pushing raw.
