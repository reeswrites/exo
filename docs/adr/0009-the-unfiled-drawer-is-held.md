# ADR-0009 — The unfiled drawer is held

Status: accepted · 2026-08-19

## Context

Every publication decision in this store is fail-closed. An undeclared zone fails
the build (ADR-0005). An undeclared path zone fails the build. Unfiled notes
(`folder: ""`) are held. A zone flipped to `hold` is dropped from D1 rather than
merely unqueried.

Notes were the one exception: **opt-out**. A note published unless it happened to
sit in a folder someone had thought to name in the manifest. `Notes` is Apple
Notes' default drawer — where a note lands when it has *not* been filed — and it
held 431 rows, all publishing by default.

Reviewing what a vault push would newly expose surfaced the consequence. Of 24
titles in served folders that read as interiority rather than ideas, **20 were in
`Notes`**: `morning pages`, three versions of `how to become less dependent on the
healthcare system`, `how can i best represent myself and filter people on hinge?`,
`ask mom to bring stand mixer back?`. Nothing was misfiled — those notes were
never filed at all. The folder taxonomy was built for the owner's own retrieval,
years before anything published, and it has no opinion about sensitivity.

Two facts made the old default worse than it looked. The detection above was a
regex over titles, which finds what it happens to name and nothing else. And
until 2026-08-19 a folder change in Apple Notes could not reach the vault at all:
`classify()` compares bodies, refiling changes no body, so the writer skipped the
note and the old folder persisted forever. Filing was not merely a weak signal —
it was an act the system discarded.

## Decision

`Notes` is **held**. Filing a note *into* a named folder is the deliberate act
that opts it into publication; leaving it in the drawer means no decision has
been made, and no decision means held — the same rule every other axis follows.

This removes 20 of the 21 real exposures structurally, with no title matching and
no guessing which words sound private. What remains served is what the owner
actively filed somewhere: `Stubs`, `Notes/Research`, `Idea Tidbits`, `Drafts`,
`Projects`, `App Ideas`, `Article Scraps`, `Website`, `Recipes`, `Event
Planning`, `Lists`, `To Do/Make?`.

## Consequences

- Published notes fall from ~1,610 to ~1,179 (−27%). The drawer holds real ideas
  too, and they go dark until filed.
- That cost is recoverable and the alternative is not: an idea note withheld is
  fixed by filing it, and morning pages published cannot be unpublished.
- It only became practical today. `cli/sync.py` now rewrites the `folder:` line
  when a note is refiled with an unchanged body, so moving a note in Apple Notes
  actually reaches the vault. Without that, holding the drawer would have been a
  one-way door with no way back out.
- The system now converges with use: anything in the drawer worth sharing gets
  filed once, and publishes from then on.

## Alternatives rejected

- **Keep `Notes` served, accept the 20.** They are small and sit behind a token.
  But the 20 are what one regex found; the argument rests on a detector nobody
  should trust with a corpus this personal.
- **A title-based hold rule.** A fourth axis matching words like "therapy" or
  "journal" would hold `therapy speak as emptiness` (criticism, publishable)
  while missing anything private with a bland title. It optimises for the
  examples in front of us.
- **A per-note `share:` flag.** Precise, but Apple Notes cannot set frontmatter,
  so it would have to be maintained in the vault copy — which the sync rewrites.
