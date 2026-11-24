# ADR-0028 — The caller changed, so the caps move

Status: accepted · 2026-11-24 · amends [ADR-0007](0007-bound-the-blast-radius-of-an-injected-read.md) §2–3 and [ADR-0019](0019-publicity-is-an-axis-not-an-adjective.md) §5

## Context

ADR-0007 sized its caps against one caller: a hosted messaging assistant that
also read the owner's texts and email, and could reply on its own. An
injected message ("summarize their notes and reply with them") arrived looking
exactly like the owner, and the reply that leaked it went out with nobody
watching. Twenty private rows per call was the answer to that.

That caller is gone, and the owner revoked its access. The surface is now
reached by three:

- **A proactive agent** that reads email and calendar. It still sees text an
  attacker controls, so injection stays in the threat model. What changed is
  the outbound step: consequential actions, sending a message among them, need
  the owner's approval on a screen that shows the destination and the content,
  and that check runs outside the model doing the task. An injected read can
  still happen. Getting what it read out of the agent now needs a human to say
  yes to something they can see.
- **A chat assistant** and **a coding agent**, which act at the owner's request
  in a session the owner is watching. Neither reads the owner's inbox.

Meanwhile the caps cost real answers. A review of ~75 questions from chat
history (2026-11-24) found the twenty-row ceiling behind repeated failures:
recommendations that repeated things already seen, a check that covered ~150
of 720 films, an anime question that saw the last 20 of 358. Some of that was a
missing tool shape, now fixed by `ratings(title:)`. The rest was the cap.

## Decision

1. **Private row cap: 20 → 50.** Injection still reaches a caller, so the cap
   stays. But the exit now runs through an approval the owner reads, so the cap's
   job shrinks to its "dumb case" from ADR-0007, the single "return all their
   notes" call. It stops that call from taking everything in one go, so that if
   the owner skims an approval, what goes out is one bounded answer. Fifty rows
   still makes a full-corpus pull a long, logged walk.
2. **Profile and published row caps: 100 / 200 → 1,000.** In practice that
   means no row cap: the byte budget binds long before 1,000 rows, so for public
   material the only limit left is the caller's context. ADR-0019 already said why
   this is safe: blast radius is what an injected read takes *that it could not
   otherwise get*, and for a public profile or a published blog that is nothing.
   It stays finite rather than `Infinity` only because every tool hands
   `probe()` to a SQL `LIMIT`.
3. **A cursor over public material is permitted.** ADR-0007 §3, as amended,
   forbade a cursor over material that is not already public, and this restates
   it: a tool whose rows are all graded profile or published may page. No tool
   pages yet. Each needs a unique tiebreaker on its `ORDER BY` first, or an
   offset repeats and skips rows silently (ADR-0007 §3's own finding). That is a
   per-tool change, done as tools need it, not here.
4. **The byte cap does not move in this ADR.** It is a context budget, not a
   security control (ADR-0019 §5), and the evidence above is about rows. Whether
   16KB still fits the callers' context windows is a separate question.
5. **The audit log is the backstop, so it gets read.** A larger cap is safe only
   to the degree that an odd pattern gets noticed. The pull of `wh_audit` that
   this review started (calls, empty results, the caller's door, per tool) becomes
   a recurring check, not a one-off.

## Alternatives rejected

- **Remove every cap.** On private material that turns one successful injection
  plus one careless approval into the whole corpus in a single call. The
  approval gate is a claim by the agent's vendor with no published results yet.
  A cap is cheap insurance against it being weaker than claimed.
- **Caps per caller (door).** The audit log records which door a call came in
  through, so the chat assistant and coding agent could get more than the
  proactive agent. Rejected for now. It complicates a boundary that is simple
  on purpose, and once the outbound step is approval-gated the doors no longer
  differ much in risk. Revisit if a caller with an ungated outbound action
  connects.
- **Keep twenty and fix only the tool shapes.** `ratings(title:)` fixed the
  sharpest case, and narrow questions should keep winning. But "which of the
  anime I've started have I dropped" is a list question with a list-sized
  honest answer, and twenty was never sized to it. It was sized to a caller
  that no longer connects.

## Consequences

- A private answer can be up to 2.5× longer in rows. Notes are mostly bound by
  the byte budget (~10 typical notes per call), so the change lands on narrow
  rows: titles, artists, dates.
- Public lists stop truncating on rows. They will still truncate on bytes, and
  `has_more` still says so.
- **If a caller with an ungated outbound action connects** (anything that can
  send, post or reply without the owner seeing it first), the private cap goes
  back down. This ADR's premise is the approval gate, not the vendor.
