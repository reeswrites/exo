# ADR-0023 — A slice says what it is a slice of

Status: accepted · 2026-08-25

## Context

ADR-0022 landed a day ago. It says a truncated list means something different on
each axis, so the caller picks the axis and the answer names it. That was right
and it was half the sentence.

Naming the axis says *which end* of a list twenty rows came from. It does not
say twenty **of what**, and the second number is the one an assistant was
actually missing. Asked about the owner's listening, one read `taste`, got
twenty artists, and reported back that the listening was narrow. It was not
narrow. It was twenty rows off a corpus of thousands, and nothing in the answer
said so, so the reader supplied the only denominator it had: the answer itself.

That is the second time this shape of failure has been read back to us, which is
why it is an ADR and not a patch. Pulling the one call apart, it was not one
bug. It was three, and each is a different way for a tool to be narrower than it
sounds.

**A tool whose population is pre-filtered, and never mentions it.** `taste` read
`t2_affinity`, which is derived with `WHERE plays > 100 AND length(artist) >= 4`
in front of it. So the tool that described itself as "what the owner actually
listens to" could only ever return heavy rotation. Everything played fewer than
a hundred times — which is nearly everything, and is precisely where a taste
gets interesting — did not exist as far as the surface was concerned. The floor
lives in `t2.py`; the description made no mention of it, because descriptions
are written next to the tool and the floor is two files away.

**A colour column deciding a whole tool's grade.** `t2_affinity` joins
`t0_music` to `t1_notes`, so by ADR-0019 §2 it is `private`, so `taste` was
`private`, so `taste` answered twenty at a time. But `t0_music` is `profile` and
allows a hundred. The tool was paying the notes' grade for one integer —
`mentions` — that no caller had asked for and most questions do not want. Eighty
rows, on every call, for a column riding along.

**A closed vocabulary presented as free text.** `collection` matches `topic`
against `genre`, and `genre` is a column somebody typed into a spreadsheet by
hand: six buckets across the whole shelf. A caller searching for a genre that is
not one of the six got zero rows and no explanation, which reads as *he owns
none of that*. What it means is *this sheet has no bucket by that name*. Those
are different claims and the answer could not tell them apart.

All three have the same shape. The surface returned a slice and let it be read
as the whole. ADR-0013 §2 has said since the surface existed that conveying our
own shape to the agent is our job; these are three places where it was not being
done, and no amount of care on the reader's side could have recovered a number
that was never sent.

## Decision

**Where a tool's rows are drawn from a population the caller cannot see, the
answer counts that population.**

The envelope key is `scope` — already in use on `saves` and `backlog`, now the
name for this everywhere. It is a sentence, not a schema, and it says how many
and over what span:

```
taste            "40,561 plays by 2,314 artists, 2016-04-02 → 2026-06-02"
around_the_time  "2026-03-01 → 2026-03-31 held 12 notes · 61 artists over 704
                  plays · 9 films · 2 books; these rows are the head of each"
```

`scope` is not `has_more` said twice. `has_more` is a boolean about this page;
`scope` is the denominator, and a denominator is what the failing reads were
inventing. Twenty artists is a fact about the cap. Twenty of 2,314 is a fact
about a person, and it is the one that stops the sentence "his listening is
narrow" from being written.

### A tool is graded on the record it is about

An optional column drawn from a more private zone is **opt-in**, and costs its
grade only on the call that asks for it. `taste` declares both zones in `reads`
and narrows through `readsFor`: the default call reads `t0_music` and answers at
`profile`, and `taste(with_mentions: true)` reads the notes too and answers at
`private`. Same tool, same invariant — a call is as public as the least public
zone *that call* reads — applied to a flag rather than to an enum.

This makes the flag the first `readsFor` whose default is the *looser* read,
which the test suite had assumed away: it asserted that every `readsFor` returns
the full union for an argument it does not recognise. That is right for an enum,
where an unknown value could mean any of them and the safe reading is the
tightest. A boolean has no unknown value — absent means unset, and any
non-boolean a caller sends is truthy, which adds the zone and tightens the
grade. The invariant that has to hold is the one that was meant: `readsFor` may
never return a zone outside `reads`, and there is a shape that reaches the union.

### A closed vocabulary is returned, not guessed at

Where a filter matches against a hand-maintained field with a small fixed set of
values, the answer carries the set. `collection` returns `genres` on every
answer, and a `topic` that matches nothing returns the vocabulary and says that
a word outside it cannot match whatever is on the shelf. This is the same move
`saves` already makes when called with nothing to narrow by — return the axes,
not twenty arbitrary rows — applied to the miss instead of to the empty call.

### What this does not do

It does not raise a cap to make an answer feel complete. `taste` returning a
hundred rows instead of twenty is a consequence of grading it correctly, not a
goal; ADR-0007's caps are untouched and there is still no cursor. A hundred
rows off two thousand artists is just as much a slice as twenty was. The fix is
that it now says so.

It also does not fix the genre buckets. Six coarse values is a fact about a
spreadsheet in an instance, and the engine has no business inventing a taxonomy
over someone's record (ADR-0014). What the engine owes is that the coarseness be
visible from the answer.

## Consequences

- `scope` joins `order`, `returned_count` and `has_more` as an envelope fact an
  assistant reads without parsing prose. A new tool over a filtered or windowed
  population is expected to carry it.
- `taste` grew filters, a window and three axes, because a tool with no
  arguments has only one answer and twenty rows was all of it. `artist` makes
  "do they listen to this at all" askable; `since`/`until` makes "lately" a
  different question from "ever"; `order` picks between most-played, last
  reached for, and fallen out of rotation longest ago.
- `t2_affinity` stops being what the taste answer is built from and becomes what
  enriches it. `toolzones.py` follows: `required=("t0_music",)`,
  `enriches=("t2_affinity",)`. Held, `taste` still answers.
- A derived zone with a filter in front of it is a population, and any tool
  reading one either states the filter or reads through it to the ground truth.
  `taste` now reads the scrobbles.
- ADR-0022 stands, with its subject widened: the answer names the axis it was
  sorted on, and counts the population it was drawn from.
