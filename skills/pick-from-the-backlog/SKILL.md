---
name: pick-from-the-backlog
description: Recommend what the owner should watch, read, listen to or cook next, drawn from what they already queued, started and rated rather than from general opinion. Use it when they ask what to watch tonight, what to read next, what to pick off their own pile, whether they would like a specific thing, or want their stalled shows and abandoned books surfaced. Not for recommending things they have never heard of unless they ask for that.
---

# Pick from the backlog

The record already knows what they queued, what they abandoned halfway, what
they own, and how they actually rate things. A recommendation built out of that
beats one built out of general opinion, and it is the only kind this surface can
honestly make.

Two mistakes make an answer wrong even when every row is right. Both are
predicted by the facets, and both are what this skill exists to prevent.

## Needs

| tool | without it |
|---|---|
| `backlog` | the whole premise is gone; fall back to `watching` and say so |
| `watching` | you lose the strongest candidates — say the half-finished pile is not visible |
| `taste_summary` | **do not report any rating as high or low.** Give the number and its scale, flat |
| `ratings` | judge from `verdicts` and `reviews` instead, and say the sample is small |
| `verdicts`, `reviews` | you have numbers and no reasoning; do not infer why they liked something |
| `collection` | drop the owned-it-already check |
| `taste`, `taste_profile` | skip the stated-versus-revealed reading |
| `medium` | just call the underlying tools; this one is a convenience |

## The mistake about intent

`backlog` and `saves` are `class: intent`. A queued book is a decision they made
once, not a thing they did, **and nobody prunes these lists.** A shelf entry from
four years ago may be a want they no longer hold, a book they finished
elsewhere, or a title they now actively avoid.

So:

- Never say they read, watched or liked something because it is in `backlog`.
- Never say they read something because it is in `saves` — a save is attention,
  weaker still, and often unopened.
- `collection` is what they own, which is not what they consumed. Owning a
  record and wearing it out are different claims; where the tool can rank owned
  music by plays, use that rather than assuming.
- The one intent row that is genuinely strong is a *started and abandoned* one.
  `backlog` kind `'resume'` and `watching` with `status:'stalled'` are the best
  candidates in the record, because the decision to begin was already made and
  the cost of restarting is low. Lead with those.

Age is the useful axis and the default hides it. `backlog` returns newest first;
`order:'oldest'` is what digs up the thing that has been sitting, which is
usually the real question.

## The mistake about scale

`ratings` and the per-medium scales are `kind: judgement`, and a judgement is
unreadable without the scale it was made on. Scales here differ per medium and
run high.

**Call `taste_summary` for the medium before you characterise any rating.** It
returns how that scale actually behaves — the median, the spread, where the mass
sits. Without it you will report an average score as praise.

Then:

- Never compare a rating across media. Each medium's scale comes from wherever
  it was recorded, they have different ranges and different medians, and a
  number lifted out of one and set beside a number from another is a comparison
  the record never made.
- Each row returns its own scale. Quote it alongside the number, every time.
- `ratings` is ordered highest-first by default, so a truncated answer is the
  **top** of the list and not a sample of it. Say which you are looking at, and
  use `order:'recent'` when the question is about lately rather than about ever.
- `facets` answers "which kinds do they rate highest" where a page of individual
  rows cannot — a rollup is a summary, a truncated list is a page.
- A mean over two data points is not a preference. Where the tool reports a
  count, read it before you report the mean.

## Stated against revealed

`taste_profile` is what they *say* they like; `taste` is what they actually
play. When those two disagree, the gap is usually the most interesting thing you
can tell them — say it, rather than smoothing it into one recommendation.

`verdicts` and `reviews` are `class: authored`: their own reasoning, in their own
words. That is the highest-signal material for *why* something landed. Quote it
rather than paraphrasing, and prefer it over inferring a reason from a number.

## Answering

Three or four candidates, not a list of twenty. For each one:

- where it came from — queued, started and stalled, owned, or already rated
- how long it has been sitting, when the row carries a date
- one line on why it fits, anchored to something they wrote or something they
  measurably play
- the honest caveat when there is one: filed years ago, rated once, or a scale
  you could not read because `taste_summary` was not available

If the pile is empty for what they asked, say so and say which pile you looked
in. Do not substitute a general recommendation without naming that you switched.
