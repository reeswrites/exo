# ADR-0024 — A denominator is a source, not a calculation

Status: accepted · 2026-08-25

## Context

The record has held television for months: 358 shows and 7,719 episodes off
Trakt, one row per show, `episodes_watched` as a measure of how far each got.

It cannot say how far. `episodes_watched` is a numerator with nothing under it.
Five episodes is a third of a cour, or a whole short, or the point at which
somebody quietly stopped — and no tool on the surface could distinguish those,
because Trakt records what was watched and holds no opinion about how much there
was to watch. Asked which shows had been abandoned, the honest answer was that
the question was not hard, it was unanswerable.

Two smaller failures fell out of the same hole, and both had been visible for a
while without being read as the same thing:

- **`medium(name:'tv')` said "the owner does not rate tv item by item."** That
  was a true sentence about Trakt and a false one about him. An assistant told
  it stopped looking, which is exactly what a confident wrong sentence is for.
- **`around_the_time` labelled the television number `episodes` on a scale of
  `episodes`.** A show appears in a window because its *last* episode did, and
  the number beside it is every episode ever watched of it. So `5` on a June row
  means "five in total, the last of them in June" — and nothing said so, in a
  tool whose entire job is to describe one month.

The fix for all three is the same object, and the interesting question is where
it lives.

## Decision

**A missing denominator is a missing source. It is ingested, not computed.**

MyAnimeList exports, per list entry, the three things Trakt does not have: a
score on 1-10, the owner's own status (Watching / Completed / On-Hold / Dropped
/ Plan to Watch), and `series_episodes` — how long the thing is. That lands as
`t0_anime`, its own zone, written by its own loader, exactly like every other
consumption source. It is a *format* a stranger could hold, so it ships in the
engine (ADR-0014).

Three consequences, and they are the whole of the decision:

### 1. The arithmetic happens inside one row, never across the join

MAL files each season as its own entry; Trakt keeps one show with many seasons.
So MAL's "12 episodes" and Trakt's "22 watched" do not measure the same object,
and a tool that divided one by the other would report a show as finished, or
overrun, on the strength of a shape mismatch nobody declared.

`watching` therefore computes `watched / total` **within a single MAL row**,
where both halves count the same entry. The two zones are joined on a normalised
title (`exo/loaders/titles.py`) for exactly one purpose: to borrow a fresher
*date*, because Trakt is timestamped per watch and a MAL list edit is not. A
wrong date on one row is a wrong row. A wrong denominator would be a wrong
verdict about a person's attention.

That is also why the key is deliberately dumb — no season-stripping, no fuzzy
distance. A near miss costs a fallback to the list's own date. A false match
would cost the arithmetic, and those are not symmetrical errors.

### 2. `stalled` is derived and says so; `dropped` is declared and says so

Two facts about an unfinished show that a single status column would flatten:

    dropped, on_hold, plan_to_watch    the owner's own word, off the list
    stalled                            nobody's word — no episode in 180 days,
                                       with episodes still left to watch

Abandoning something on purpose and drifting away from it are different facts
about a person, and only one of them is quotable back to them. So a `watching`
row carries both: `declared` is what they said, `status` is what follows from
it, and nothing is ever *declared* stalled. The window is one number in one
place (180 days — a season and a half: long enough that a gap is not a break
between cours, short enough that a show abandoned last winter surfaces before it
is a year gone).

### 3. What has no denominator says so, rather than being judged without one

MAL writes `series_episodes = 0` for a series whose length is not settled yet.
Such a row can never be called stalled at any age, and `watching` states how
many of them there are rather than letting them read as titles nobody stopped
watching. The same statement, one level up, rides on every answer: **only the
anime shelf carries totals.** A reader who sees forty anime measured and no
drama measured will otherwise conclude he finishes everything else — the same
error ADR-0023 is about, arriving through a coverage gap instead of a cap.

## Consequences

- `t0_anime` is a new zone and must be declared in `serve-manifest.json` like
  any other; fail-closed still applies. It is graded `private` by default. MAL
  profiles are commonly public, but publicity is a claim about the outside world
  that this repository cannot verify (ADR-0019), so it is one line for the owner
  to change and not a default to inherit.
- `t0_tv` gains a `match_key` column. Both loaders mint explicit ids, so no row
  re-mints and the ledger stays quiet (CONTRIBUTING).
- `ratings` gains `medium:'anime'` on a 1-10 scale — the first ratings
  television has ever had here — and `medium(name:'tv')` now points at them
  instead of denying they exist.
- An instance with no MAL export loses `watching` from the advertised surface
  and nothing else: `medium` drops anime from its directory rather than failing
  on a table that is not there, and the tv note reverts to the old sentence,
  which is true again once nothing contradicts it.
- Television is still only half measurable, and every answer says which half.
  Fixing the other half means a source that knows how long a drama is; that is
  another loader, not a smarter query.
