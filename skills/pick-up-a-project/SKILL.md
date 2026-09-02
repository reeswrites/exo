---
name: pick-up-a-project
description: Work out what the owner should return to in their own repos, and reconstruct where they left it — what is unfinished, what went cold, what a project already decided about itself, and which questions it left open. Use it when they ask what to work on, what they were doing on a project, what they abandoned, what is left to do, or when they are about to reopen something they have not touched in a while. For their own repos, not for reading a codebase.
---

# Pick up a project

The workshop tools hold prose and metadata about the owner's repos: what each
one claims to be, what was committed and when, what documents it carries, and
what is visibly unfinished in it. **No source code is in this store** — subjects,
paths and marker text only. Never imply you read the code.

The failure this skill exists to prevent is reading a stale pointer as current
state. `project_open` is `kind: pointer`, and pointers go stale silently.

## Needs

| tool | without it |
|---|---|
| `projects` | you cannot see the shape of the workshop; ask which repo they mean |
| `project_activity` | the dated work log is gone — do not guess when something died |
| `project_open` | you can still say what a project is; not what is left in it |
| `project_docs` | you lose the reasoning; report state without the why |
| `drafts` | skip the half-written prose behind a repo |
| `open_threads` | skip the unclosed questions; it takes no filter, so it is all or nothing |
| `notes_on`, `whats_relevant` | skip the notes join |

## Read status as heat, not as judgement

`projects` grades each repo by how recently it was committed to — active, warm,
stalled, dormant. That is a measurement of heat and nothing else.

- **Dormant is not failed.** Everything that simply shipped is dormant. So is
  everything finished, everything archived, and everything that never needed
  another commit.
- **Deliberately shelved is not the same as gone cold.** Where a repo is filed
  under a group meaning hiatus or similar, that filing is a decision the owner
  made, not an inference. Report it as their decision.
- **Call `projects` with no arguments first.** It ranks by commits in the recent
  window rather than by last-commit date, which is the honest default: half a
  set of repos can be initialised in one week, and sorting by recency floats a
  one-commit import above a year of real work.

## Reconstruct where they left it, in this order

1. **`project_activity` for the repo.** Commit subjects, dated. This is the
   closest thing here to a work log, and unlike a note it cannot be stale — a
   commit is written at the moment of the work. Read the last ten before you say
   anything about what the project is in the middle of.
2. **`project_docs`.** READMEs, glossaries, architecture decision records, plan
   documents. This is where a project states what it is *for*. An ADR is the
   owner arguing with themselves and recording who won — the same category as
   their notes and usually more decided, so read it before proposing anything it
   already settled. Search by topic across repos to find which project answered
   a question; pass a repo to read what it says about itself.
3. **`project_open`.** TODO and FIXME markers, unchecked plan items, uncommitted
   files. **Treat this as a trail, not a backlog.** Nobody prunes markers, so an
   old one may name work that was done another way, or a decision that was
   reversed. Cross-check every marker you plan to surface against
   `project_activity` around and after its neighbourhood, and say when you
   cannot tell whether it still stands. An uncommitted file is the exception —
   that is live state, and it is usually the single most useful thing to report.
4. **`drafts(topic:)` and `open_threads`.** The half-written prose and the
   unclosed questions the repo itself does not carry. `drafts` takes a topic;
   `open_threads` takes no arguments at all, so it returns the whole list and
   you filter it by reading. Do not claim you queried it for this project.

A marker is a note written to oneself at the moment of choosing not to do
something. That makes it excellent evidence of *intent* and poor evidence of
*state*. Use it for reading what they cared about, not for building a task list.

## Answering

Lead with the one thing that would actually restart the work — usually an
uncommitted file, a stalled repo with a clear next marker, or a plan document
with unchecked items and recent commits around them.

Then, briefly:

- what the project is, in its own words from `project_docs`, not your summary
- when it was last worked on, and on what — dated, from commit subjects
- what is unfinished, each item flagged as *still open* or *cannot tell*
- what it already decided, where an ADR or plan answers a question they are
  about to reopen

If they asked what to work on generally, offer two or three, and say which axis
you chose on — most nearly finished, longest untouched, or most recently warm.
Those give different answers and the choice is theirs.

Never propose work that a document in the repo already argued against without
naming that document. That is the one thing this surface can do that reading the
code cannot.
