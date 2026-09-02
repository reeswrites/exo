---
name: recommend-media
description: Suggest what the owner should watch, read, listen to or cook next — either from the pile they already queued and started, or from outside it. Use when they ask what to watch tonight, what to read next, what to pull off their own shelf, whether they would like a specific thing, or want their stalled shows and abandoned books surfaced. Events are a different question; this is media.
---

# Recommend media

Two answers hide behind one question, and they use different halves of the
record. **Decide which one is wanted before you call anything.**

- **From the pile** — things they already queued, started and abandoned, or own.
  Inside the record. This is usually what "what should I watch tonight" means,
  and the strongest candidates in the whole record live here.
- **From outside the pile** — something they have not consumed. Discovery. This
  is what "recommend me something new" means.

When the question does not say, do the pile first and say that is what you did.
An abandoned book is a better recommendation than a stranger, and it costs one
call to check.

## Needs

| tool | without it |
|---|---|
| `consumption` | you lose the denominator and how current the record is; say the shape is unknown |
| `medium` | the same in one call, plus the scale and what they own — prefer it when the question names one medium |
| `ratings` | judge from `verdicts` and `reviews` instead, and say the sample is small |
| `backlog`, `watching` | the pile route is gone; say so rather than switching routes silently |
| `taste` | no music membership check and no revealed-preference signal |
| `taste_summary` | **do not call any number high or low.** Give it with its scale, flat |
| `verdicts`, `reviews` | you have numbers and no reasoning; do not invent why something landed |
| `collection` | drop the owned-it-already check |
| `releases` | the only outside pool for music is gone; do not substitute your own |
| `criticism` | drop the what-is-being-said-now half |

## Read the shape before you form a candidate

1. **`consumption(medium)`** — the total and `last_logged`. If `last_logged` is
   old, every conclusion below is about who they were, not who they are. Say so
   in the answer rather than quietly discounting it.
2. **`ratings(medium, order:'recent')`** — the short-term window. This is what
   they are like *now*.
3. **`taste(order:'recent')`** — for music, or for cross-domain signal.

Two traps in step 2.

**The default order is not a sample.** `ratings` sorts by rating, highest first,
so a truncated default answer is the **top** of the list. Building a
recommendation from it and describing it as "recently" is a false statement made
of true rows. Pass `order:'recent'` and say which you asked for.

**There is no `limit`, and there is no cursor** (ADR-0007). The row cap is set by
how public the answer is, not by what you ask for. A `limit:` you pass is
silently dropped, and you are left believing you saw more than you saw. When an
answer is too coarse, **ask a narrower question** — a medium, a `min_rating`, a
`topic` — rather than trying to page.

## Route A — from the pile

`backlog` and `saves` are `class: intent`. A queued title is a decision made
once, not a thing done, **and nobody prunes these lists.**

- Never say they read, watched or liked something because it is in `backlog`,
  and never because it is in `saves` — a save is attention, weaker still.
- `collection` is what they own, which is not what they consumed. Where the tool
  can rank owned music by plays, use that rather than assuming.
- **Started and abandoned is the strongest row in the record.** `backlog` with
  `kind:'resume'`, and `watching` with `status:'stalled'`. The decision to begin
  was already made and the restart cost is low. Lead with these.
- Age is the useful axis and the default hides it. `backlog` returns newest
  first; `order:'oldest'` digs up what has been sitting, which is usually the
  real question.
- A high rating on something already logged is a **rewatch or a reread**, not a
  mistake. Offer it as one and give the date you have.

## Route B — from outside the pile

### There is no membership check. Know what screening exists.

Nothing on this surface takes a list of titles and answers "consumed or not".
Do not assume one, and do not invent a call. What actually exists differs by
medium, and it is uneven:

| medium | the screen you have | what it cannot do |
|---|---|---|
| music | `releases` already removes what they scrobbled or own and reports how many it removed; `include_heard:true` keeps them. `taste(artist:'X')` asks whether one act is in the record at all | neither answers a specific *track* |
| tv, anime | `watching` per show, with status and how far through | says nothing about shows never started |
| books | `backlog(kind:'read'\|'resume')` and `ratings(medium:'books')` | no per-title check outside those |
| film | **nothing** | `ratings(medium:'films')` is capped and ordered, `reviews` searches by topic. You cannot check whether they saw a given film |

So for film especially: propose, and say plainly that you could not check. Do
not phrase a proposal as though it were screened.

### Absence is a gap, not an empty queue

The tools say this themselves, and quoting their note is better than restating
it, because a restatement drifts and theirs cannot:

- `medium(name:'film')` and `watching` return a note that the watchlist was
  never exported, so films missing from the record are missing **data**, not an
  empty queue.
- `watching` returns a note that only the anime shelf carries episode totals, so
  no other show can be called finished or unfinished.

Say **"not in the record"**, never "you haven't seen this". Those are different
claims and only one of them is supported.

## Read the scale off the row, and know what is calibrated

`ratings` returns the scale on every row and `medium` returns it as
`rated.scale`. Use what came back. Do not carry a scale in your head between
calls, and do not compare a number from one medium with a number from another —
the scales come from wherever each was recorded, with different ranges and
different medians.

**Calibration exists for some media and not others.** `taste_summary` takes
`kind` and answers for `dining`, `beer` and `clusters` — that is the whole list.
There is no calibration document for film, books or anime. For those, report the
number with its scale and do not characterise it as high or low.

`facets` is the rollup that answers "which kinds do they rate highest" where a
capped page of rows cannot — and it currently covers **beer only**. A mean over
two data points is not a preference; read the count before you report the mean.

## Do not

- **Do not rank by preference.** This surface answers what is true and does not
  decide what to do about it (ADR-0013). Every ordering it offers is over a
  measured fact — plays, rating, date, count. Attach those facts and leave the
  judgement to the agent, which is exactly what `releases` says of itself.
- **Do not page.** There is no cursor. Narrow the question.
- **Do not substitute general opinion silently.** If the record cannot answer,
  say so, then say you are switching to what you know from outside it.

## Answering

Three or four candidates, not twenty. **Cite the receipt** — every suggestion
names the row that produced it:

- where it came from: queued, started and stalled, owned, already rated, or an
  outside pool
- how long it has been sitting, when the row carries a date
- one line on why it fits, anchored to something they wrote or measurably play
- the honest caveat: filed years ago, rated once, a scale you could not
  calibrate, or a medium you could not screen at all

`verdicts` and `reviews` are `class: authored` — their own reasoning, in their
own words, and the best material for *why* something landed. Quote them rather
than inferring a reason from a number.
