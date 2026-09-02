---
name: trace-an-idea
description: Trace how one of the owner's own ideas developed across everything Exo holds — published posts, drafts, notes, saved links, conversation threads and open questions — then surface the reversals, contradictions and unfinished threads between them. Use it when they ask what they have written about something, how their thinking on it changed, whether two of their own pieces connect, or ask you to check their past work before they write. Not for research about the outside world; only for their own material.
---

# Trace an idea

The owner's material is spread across surfaces that do not talk to each other: a
published blog, private notes, half-finished drafts, saved links, long
conversation threads. Exo is the one place they join. They cannot see across
them. You can.

What they want is almost never a summary. It is the **drift**: where a position
moved, where two dated pieces contradict each other, where a private note
quietly reverses a published one. They already know the topic. The joins are the
part they cannot get themselves.

## Needs

| tool | without it |
|---|---|
| `whats_relevant` | the sweep loses its best opening move; start at `notes_on` and `posts` instead |
| `posts` | you can still trace the private side, but say that the published half is not in scope |
| `notes_on` | lean on `whats_relevant`, and say the map of what exists is missing |
| `drafts` | the note-to-post gap is invisible; do not claim a thing was never written |
| `saves` | you lose the borrowed-frame finding entirely; drop that section rather than guessing |
| `recent_topics`, `thread` | the freshest layer is gone; say the trace stops at the last written note |
| `open_threads` | report the unfinished questions you can see in drafts and stop there |
| `around_the_time` | only matters when they asked about a period rather than a topic |

An instance offers a subset of the surface and a held zone retires its tool
(ADR-0020). Check what is actually listed before you plan the sweep, and say
which surfaces you could not reach — an absent tool is not an empty record.

## The sweep, in this order

Each step answers a different question, and the early ones tell you what to ask
the later ones. Do not jump to the surface that looks most relevant.

| order | call | the question it answers |
|---|---|---|
| 1 | `whats_relevant` | everything they have written that bears on this, by meaning. The best single first move |
| 2 | `notes_on` (titles first) | what exists privately — a map, before you spend calls on bodies |
| 3 | `notes_on` / `posts` with `full:true` | what they actually said, as opposed to what the title suggests |
| 4 | `posts` | what got finished and published, with its live URL |
| 5 | `drafts` | what stalled between the note and the post. `stale_days` finds what went cold |
| 6 | `saves` | what they *read*. Input, never opinion |
| 7 | `recent_topics`, then `thread` | what they argued out loud. Fresher than notes, which lag an act of capture |
| 8 | `open_threads` | questions they asked themselves and never closed |

Two notes on the conversation surfaces. `recent_topics` matches on the title,
the gist and where the thread landed, so a short topic word works better than a
sentence; `min_turns` with no topic is the other useful shape. Turn count is the
signal a title cannot carry — a long thread is a preoccupation, a short one is a
glance, and a thread they opened and abandoned after six turns is itself a
finding.

Fetch two or three things in full rather than skimming ten. Search excerpts show
you the sentence that matched; the reversal is three paragraphs later.

## Date everything

Pull the date on every row and sort chronologically before you write a word.
Almost every real finding falls out of the ordering:

- a private note dated *before* the public position it contradicts
- vocabulary from a save appearing in their own writing weeks later
- two of their own notes, six weeks apart, that cannot both be true
- a question first asked years ago and still on the open list

Some rows carry no date and say so — `verdicts` has none at all, and a `saves`
row dates when it was bookmarked, not when it was read. Where a date is missing,
say so rather than guessing a position in the sequence.

## Provenance — the rule that matters most

Attributing a machine's polished framing back to the owner as their own
conclusion is the worst thing this skill can do, because they will believe it.

You do not have to guess. Every tool declares a `class`, and the tools that mix
voices label the mixture themselves:

- **`class: authored`** — `posts`, `notes_on`, `drafts`, `verdicts`, `reviews`.
  Their words, deliberately written. Quote these as theirs.
- **`class: derived`** — `whats_relevant`. A machine matched and lifted these
  spans. The verbatim span inside is theirs; the *selection* and any framing
  around it is not. Use it to find the source, then quote from the source.
- **`class: dialogue`** — `recent_topics`, `thread`. Half of a thread is another
  model. `thread` returns `dialogue_note` and speaker tags with
  `include:'dialogue'`, and marks any distillation with `summary_is` naming who
  wrote it. Honour both. Where the useful line came from the assistant side, say
  so plainly: *this was put to you in that thread and you moved on.* That framing
  is more useful to them than a false attribution anyway — it names an argument
  they have not answered.
- **`class: intent`** — `saves`, `open_threads`. A declared want, not a
  consummated one. A save caught their attention; it does not mean they finished
  it, agreed with it, or ever came back. Never quote a save as their view.
- **`class: world`** — `criticism`. Somebody else's writing entirely. Attribute
  it to the outlet and answer with the link.

When you cannot tell which side a line came from, say you cannot tell.

## What to look for

Three shapes are worth more than anything else you could report:

1. **A reversal.** A private note that contradicts a published position. These
   sit in the private surface, because that is where people think before they
   commit.
2. **A contradiction with dates.** Two of their own pieces that cannot both
   hold. Give both dates and let the collision stand — do not resolve it for
   them.
3. **A borrowed frame.** Vocabulary from something in `saves` turning up later
   in their own writing, unattributed. Usually invisible to them.

## Report what is missing

Absence is a finding, and it is the half a summary throws away:

- a draft referenced in a thread that `drafts` does not hold
- an open question from two years ago that nothing since has answered
- a claim with no supporting case anywhere in the record
- a topic that is all `saves` and no writing — read, never thought through

Say plainly which surfaces returned nothing. A search finding nothing is not
evidence the thing does not exist; it is evidence this record does not hold it.

## Output

Lead with the finding, not the process. Do not narrate which tools you called.

A shape that works: a short lead naming the single most surprising join, then
numbered points each anchored to a dated artifact with its link, then one
clearly labelled tension, then what could be written next. Keep it to a couple
of phone screens unless they ask for more.

If the material has a clear chronology and a position that moved, a vertical
timeline earns its place. Colour by position, not by date.

Correct yourself out loud when a later step overturns something you said
earlier. That is the skill working, not an embarrassment — it means the sweep
found something.

## When to stop

Eight to fifteen calls covers most of these. Stop when the sweep stops returning
new dates.

Writing the result anywhere — a file, a note, another system — is not this
skill's job. Hand back the prose and let them place it.
