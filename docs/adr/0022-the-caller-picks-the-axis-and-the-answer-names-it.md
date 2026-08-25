# ADR-0022 — The caller picks the axis, and the answer names it

Status: accepted · 2026-08-24

## Context

ADR-0013 §1 says the surface ranks by relevance and never by preference, and
that every `ORDER BY` in the tool table is over a measured fact — created,
plays, rating, turns, start, count. That invariant held. It also turned out to
be answering a narrower question than anyone was asking of it.

It says nothing about *which* measured fact, or about what happens when a row
carries two. Reading the whole table again, three separate failures fell out of
the gap:

**A tool that holds two facts and sorts by one answers half its questions.**
`ratings` sorted films by rating, so "what has he been watching lately" came
back as his 5-star all-timers from 2019. `reviews` sorted by watch date, so
there was no way to reach the best-argued 115 without reading all of them.
`places` sorted by rating and did not return the visit date **at all**, so a
restaurant loved last month and one loved in 2019 were indistinguishable. In
each case the other fact was already in the row.

**A truncated list means something different on each axis, and `has_more` cannot
say which.** Twenty films off 720 sorted by rating are the *top* twenty; twenty
sorted by recency are the *last* twenty. ADR-0007's caps guarantee that
truncation is frequent, and ADR-0013 §2 makes conveying our own shape to the
agent our job — this is exactly that, and it was missing.

**Two sorts were not over a measured fact after all.** `verdicts` ordered by
`created DESC`; nothing sets `created` on that zone, so every row was NULL, the
sort fell through to `id` — a content hash — and ten opinions came back in hash
order under a heading that said newest first. `recipes` ordered by title, which
is not a fact about anything, and the zone holds untitled seed templates
(ADR-0003), so `full:true` with no topic answered "give me a recipe" with an
empty shell.

And one sort was over the right fact in the wrong type. `t1_visits.rating`
arrives from a CSV column and lands in D1 as TEXT, so `ORDER BY rating DESC`
compared strings: `'9.5' > '9' > '8.5' > '10'`. Every perfect score sat at the
bottom of a list sold as best-first — while `ratings(medium:'restaurants')`,
which does cast, disagreed with `places` about the same meal.

## Decision

**Where a tool's rows carry more than one measured fact, the caller picks which
one orders the answer, from one vocabulary, and the answer says which it used.**

```
recent   newest first — the default wherever the record carries a date
oldest   the same axis reversed, for what has been sitting
rated    the owner's own rating, highest first
played   how often they actually reached for it
```

One chooser, `ordering()` in `worker/src/tools.js`, holds the vocabulary. A tool
passes a map of only the axes its rows can answer; the **first key is its
default**; the SQL never comes from the caller, which only ever picks a key out
of a map written in the file. Such a tool's answer carries `order: "<name>"`, so
a capped list is legible as the top of something rather than a sample of it.

A tool with one axis returns no `order` at all. The field means *this was your
choice and here is what it resolved to*; on a tool where nothing could have been
asked for instead, stamping it would dress a fixed sort as a decision.

An unrecognised name falls back to the default and says so in the note. A
misspelled sort is not worth failing a call over, and it is not worth lying
about either.

### This is not ranking by preference, and the line has not moved

Each axis is one measured fact, sorted plainly. What is new is that the *caller*
chooses among facts we hold instead of us choosing for them — which is less
judgment on our side, not more. `event_pitches` stays where ADR-0013 put it.

### Defaults are per tool, and recency is only usually right

Reverse chronological is the default wherever a date is the fact the tool is
about: `reviews`, `collection`, `backlog`, `recipes`, `saves`, `drafts`,
`history`, `recent_topics`. It is **not** the default for `ratings` or `places`,
where the tool exists to answer "how highly" and a rating-ordered truncation is
the useful twenty. A default is a claim about the common question, not a house
style — and now that the axis is named in the answer, being wrong about it costs
a second call rather than a wrong belief.

### A tool never advertises an axis its rows cannot answer

`verdicts` gets no `recent` option, because that zone has no date and never
will — the loader reads none. It orders by rating, and its description says the
record carries no date, rather than offering a sort that quietly degrades to
hash order.

## Consequences

- Ordering is a caller-visible part of the tool contract, so a new tool over a
  zone with a date and a score is expected to offer both and name its default.
- `order` joins `returned_count` and `has_more` as envelope facts an assistant
  can read without parsing prose — on the tools that offer a choice.
- Ties break on the tool's other fact before they fall through to `id`, so a
  page of 5-star films is dated rather than hashed. `medium`'s top-three, which
  the tie scan never saw because it caps at a literal 3, gets the same treatment.
- Ratings are cast before they are compared, everywhere. A rating that arrives
  as text is a storage detail and must never become a ranking.
- ADR-0013 §1 stands, with its subject sharpened: every `ORDER BY` is over a
  measured fact, and every tool holding more than one lets the caller say which.
