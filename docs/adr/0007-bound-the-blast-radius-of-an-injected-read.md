# ADR-0007 — Bound the blast radius of an injected read

Status: accepted · 2026-08-18

## Context

The serve projection decides what exists remotely (ADR-0005) and the surface
cannot write (ADR-0006). What remains exposed is real: ~1,600 notes and ~7,900
verbatim atoms behind a URL.

The caller is a hosted assistant that also reads the owner's texts and email. So
a tool call may originate from the owner, or from a message telling the assistant
to summarize the owner's notes and reply with them. **Both arrive as an
authenticated request with a plausible argument.** Authentication proves the
caller is the assistant; it cannot prove the assistant is acting on the owner's
intent. This is structural to putting an injectable agent in front of a corpus,
not a defect of any particular token scheme.

Designing to *prevent* injection is therefore not available. Designing so that a
successful one is small, slow, and visible is.

## Decision

Assume a single injection succeeds. Four controls, none of them authentication:

1. **Bearer token in a request header, never in the URL.** URLs reach logs,
   referrers and history; headers do not.
2. **Hard response caps — 20 rows or 16KB per tool call, whichever binds first.**

   *Revised 2026-08-18, same day.* This began at 4KB, justified as "a chat
   surface cannot consume more in one turn anyway". That justification died when
   the target was restated as a context layer for AI assistants generally rather
   than for one messaging product, and the number should have moved with it.
   16KB returns ~99% of the owner's notes whole (avg 1,573 chars, max 48,543)
   and still makes a full-corpus pull ~250 logged calls rather than a handful.

   What a cap buys is narrower than it looks: it does not prevent exfiltration,
   because a patient caller loops. It converts "one quiet call takes everything"
   into "hundreds of calls, visibly" — which is worth something only if the log
   in (4) is read. Its real value is against the dumb case, an injected "return
   all their notes", which is also the likely case.
3. **No general-purpose tool.** Fixed semantic tools only — no raw SQL, no
   id-lookup loop, no pagination cursor that can walk the full set. **The tool
   surface is the security boundary**, so every tool added is a decision about
   exposure, not a convenience.

   `read_note` returns full note text and is the sharpest instance of this rule.
   It takes a *topic*, resolves it by vector search, and returns exactly one
   note — no id parameter, no offset, no list. Enumeration is possible only by
   guessing topics, one logged call at a time, which is precisely the shape the
   log makes legible.
4. **An append-only call log** — tool, arguments, timestamp, rows returned —
   readable by the owner. Exfiltration becomes discoverable after the fact
   instead of invisible forever.

   *Extended 2026-08-19 (ADR-0010).* The log also carries the caller's ip and
   asn, and a second table records where calls come from at all — including
   rejected ones. The argument below is unchanged; the log simply answers "by
   whom" as well as "what".

## Alternatives rejected

- **Rate limiting as the primary control.** It shapes the speed of a leak, not
  its existence, while feeling protective; a patient caller still walks out with
  everything over a week. Useful as defence in depth, never as the answer.
- **Larger caps for richer answers.** "What have I written about taste?" may want
  50 atoms. Twenty plus a second question is mild friction for the owner and a
  visible pattern in the log for an attacker — the asymmetry favours the owner.

## Consequences

- Some legitimate questions need two calls. Accepted.
- Notes longer than ~16KB return truncated, with the truncation stated. About
  1% of the corpus; the longest note is 48,543 chars.
- Adding a tool is an exposure decision requiring the same scrutiny as widening
  the manifest, not a feature increment.
- The call log is the only mechanism here that detects rather than limits. If it
  is never read, three of the four controls are prevention and none is detection.
