---
title: What a log is for
type: raw
created: 2026-05-08
folder: Workshop
voice: ada
facet: about-world
---

A log is not a backup and it is not a diary. It is the answer to the question
"how did this get to be the way it is", asked by someone who has already
established what the current state is and cannot make sense of it.

Which means an append-only log with no folding rule is only half a design. The
log tells you what happened; something has to say what the sequence of happenings
adds up to right now. Keeping the current state on the record itself, next to
the log that contradicts it, is how you end up with two authorities and a bug
that only appears after a hand-edit.

I would rather compute the state every time than store it once and hope. It is
more work per read and no work at all per write, and writes are where the
mistakes live.
