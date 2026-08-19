# ADR-0006 — The remote read surface is permanently read-only

Status: accepted · 2026-08-18

## Context

The warehouse is growing a remote surface a hosted assistant can call (ADR-0005).
Adding a `capture` tool to it would be trivial and obviously useful: "remember
that" while walking, and the thought lands in the vault instead of evaporating.

The vault's `CONTEXT.md` states the constraint that makes this the wrong twenty
lines:

> The machine only ever rearranges your words. Every word of prose is yours.

second-brain's ADR-0022 extends it past the data path to the human one — a draft
never graduates into `raw/`, "however good", because waving one through because
it reads well is generated prose entering ground truth wearing your approval.
The `voice` zone is quarantined in code; `chat-logs` are `grounds: false` and
reach the self-model only by deliberate hand-promotion (ADR-0016).

A hosted assistant breaks the shape those defences assume. It produces *prose*
about the corpus, delivered by text message, away from the desk — precisely where
"yes, that's it" is most likely and a paraphrase is least likely. There is no
`query.py` in that path to exclude anything.

## Decision

The remote surface exposes **reads only, permanently**. No capture, no append, no
status flip, no write of any kind — not as an oversight to be corrected later,
but as the property that keeps the assistant outside the authorship gradient.

Capture keeps its existing route: Life Terminal's "save to brain", at a keyboard,
in the vault's idiom, where restating the thought in your own words is the
natural motion. That friction is the feature.

## Consequences

- Thoughts had away from the desk are sometimes lost. That is the accepted cost;
  a lost thought is recoverable by having it again, and grounded machine prose is
  not recoverable at all.
- Read-only bounds writes, not reads. It is not a privacy control — that job
  belongs to the serve projection (ADR-0005), which decides by omission.
- **If this is ever revisited**, the answer is not a direct write path but a
  quarantine zone: grounding-excluded, never atomized, requiring deliberate
  hand-promotion — the treatment `chat-logs` already gets. Adding the door later
  is possible; un-grounding prose that has already entered `raw/` is not.

## Alternatives rejected

- **A capture tool now.** The useful version and the dangerous version are the
  same twenty lines.
- **Write to a quarantine zone now.** Correct in shape, but it builds the door
  before the loss has been felt, and an unused door still gets walked through.
