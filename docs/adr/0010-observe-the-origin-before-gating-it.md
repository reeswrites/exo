# ADR-0010 — Observe the origin before gating it

Status: accepted · 2026-08-19

## Context

ADR-0007 bounds an injected read with four controls, none of them
authentication. A fifth was proposed: restrict *where* a call may come from, so
that a token lifted from the assistant's side is useless off-net.

The instinct was to gate on `Origin`. That does not work here and the reason
generalises: Poke, and every other hosted assistant, calls this surface
**server-to-server**. There is no browser, so there is no `Origin` header and no
preflight. A CORS-shaped control would be decoration — present in the code,
enforced by nothing. What actually exists on the request is the IP and, through
`req.cf`, the ASN, organisation, country and colo that Cloudflare fills in before
the isolate runs.

The real obstacle was that **nobody knows the answer.** Assistant vendors do not
publish egress ranges, and the ones that exist rotate. An allowlist written from
a guess has one likely failure mode: it locks out the client it was built to
protect, at whatever hour that client next calls, and the symptom is a 401 that
looks exactly like a wrong token.

The log could not settle it either. `wh_audit` recorded *what* was asked and never
*by whom*, which was tolerable with one token holder and stopped being tolerable
once there were two — the owner's laptop and the assistant — because "what was
asked" without "by whom" cannot distinguish a normal week from a compromise.

## Decision

**Split the control in two, and ship only the half that can be justified today.**

*Phase 1 — observe.* Record the caller on every POST, both outcomes:

- `wh_callers` — ip, asn, organisation, country, colo, user-agent, rolled up per
  day per (ip, asn, ua, outcome). `outcome='ok'` is the allowlist candidate;
  `outcome='denied'` is someone holding a wrong token, and that is the row worth
  an alarm.
- `wh_audit` gains `ip` and `asn`, so a call and its caller are one record.

*Phase 2 — gate, deferred.* An allowlist, in whatever form the observed data
supports, checked before the token compare so that a leaked token from off-net is
dead. Not written until the data exists to write it from.

Two properties of phase 1 are load-bearing rather than incidental:

**Rolled up, not appended.** The question is "which distinct places call", not
"how did request 4,812 go" — `wh_audit` already answers the second. An
append-only table also sits on a path anyone who finds the URL can reach, which
makes it a lever on the bill rather than a record.

**Denied writes are throttled** to one per ip per minute per isolate. Rolling up
bounds *rows*; it does not bound *writes*, and the rejected path is by definition
unauthenticated. Isolate-scoped state is the right lifetime: a cold isolate
re-logging a prober costs one row, not a hole.

Both writers are wrapped so they can never fail a request. Observation that can
take the surface down is worse than no observation.

## Alternatives rejected

- **Gate now, on a guessed range.** Fails closed against the owner, silently, at
  an unpredictable hour, with a symptom indistinguishable from a bad token.
- **Cloudflare WAF rules instead of code.** Unavailable: the Worker is on
  `*.workers.dev`, which is Cloudflare's zone, not one this account controls. WAF
  custom rules would first require moving the Worker onto a custom domain — a
  larger change than the control it would buy.
- **Per-call caller rows instead of a rollup.** Answers a question already
  answered by `wh_audit`, and turns an unauthenticated path into unbounded writes.
- **Logging the presented token on a rejection**, to tell a typo from an attack.
  It would put near-miss secrets in a table that exists to be read casually.

## Consequences

- Phase 2 is a *decision*, not a scheduled task. The data may well say not to
  gate: if the assistant's calls arrive from a wide spread inside one cloud ASN,
  an ASN allowlist admits that cloud — most of the internet — and buys close to
  nothing over the caps.
- The migration is ordered. Both writers swallow their exceptions, so a column
  mismatch fails *silently*: the surface answers normally and stops logging.
  `migrations/0001-caller-observability.sql` runs before the deploy, and a test
  pins the DDL against the INSERT.
- ADR-0007's fourth control now records more than it did. Its blast-radius
  argument is unchanged; only the fields moved.
- Even at its best, an IP gate stops a **stolen token used from elsewhere**. It
  does nothing about the threat ADR-0007 is actually about — an injected
  instruction executing inside the legitimate client, arriving from exactly the
  right address. This is defence in depth against a different attacker, not
  progress against that one.
