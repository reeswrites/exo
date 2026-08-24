# ADR-0021 — OAuth is a second door, not a second boundary

Status: proposed · 2026-08-24

## Context

ADR-0007 put the token in a request header and never in the URL. That was a
decision about **where a credential travels** — URLs reach logs, referrers and
history — and it has held. What it was not, and was never argued as, is a
decision about **which protocol proves the caller**. The header was the obvious
carrier at the time because the only caller was Poke, and Poke sends whatever
header you give it.

The bill for that arrived while attaching the surface to a second client. Header
auth is available to callers that let you set arbitrary headers, and that set is
smaller than it sounds:

| client | header auth |
|---|---|
| Poke | yes |
| Claude Code (`claude mcp add --header`) | yes |
| claude.ai web / Claude Desktop connectors | beta, gated per account — the dialog offers OAuth Client ID/Secret and nothing else |
| ChatGPT connectors | no — OAuth or unauthenticated |
| anything conforming to the MCP authorization spec | OAuth 2.1 + PKCE, with DCR optional |

MCP's authorization story **is** OAuth. A surface that speaks only bearer headers
is not a surface with unusual auth; it is a surface outside the spec's auth,
reachable by the clients willing to go around it.

Three things make that worth a decision rather than a shrug.

**It is now an engine limit, not a preference.** ADR-0014 made the code public
and the instance private. An adopter inherits the client list above. "Use a
client that sends headers" is a constraint this project imposes on other people's
records, and it is not one that follows from anything the project believes.

**Waiting is not a plan.** The one gap that could close on its own — Claude's
request-header beta — is one vendor's rollout, on their schedule, and it fixes
one row of that table. ChatGPT and every spec-conformant client stay out.

**Authentication was never the control here, which is exactly why adding some is
cheap.** ADR-0007 opens its decision with *"Four controls, none of them
authentication"*, and every one of them — the caps, the absence of a
general-purpose tool, the audit log, the exposure grades of ADR-0019 — is
indifferent to how the caller proved itself. Whatever OAuth costs, it cannot
cost us the blast-radius argument, because that argument never rested on the
credential. What it can cost is code, and a public endpoint on the one surface
whose whole case is smallness.

## Decision

**Add OAuth as an optional second door onto the existing surface. The bearer
header stays, unchanged and undeprecated.**

### 1. Two doors, and the old one is not a legacy path

An instance with OAuth disabled is byte-for-byte the surface ADR-0007 describes
today. An instance with it enabled accepts both: the header for Poke and Claude
Code, the handshake for everything else. Neither is a migration target.

This is not politeness toward existing callers. The header path has no moving
parts — no store, no expiry, no redirect, no third endpoint to get wrong — and
it should remain the path an owner can reason about completely. OAuth becoming
mandatory would mean the simplest correct configuration of this surface no
longer exists.

### 2. It is single-user, and that is the simplification that makes it safe

There are no accounts, no user table, no scopes negotiated per caller. The
instance is one person's record (ADR-0014), and modelling users would be
modelling a fact that is not true.

`/authorize` gates on the **same `AUTH_TOKEN`**, compared in constant time by
the function already in `index.js`, and shows one consent screen naming the
record being connected. The access token it issues is a different string with a
lifetime; the secret that authorises issuing one is the secret we already have.

Stated plainly, because it is the part most likely to be mistaken for something
grander: **we are implementing an authorization server to satisfy a handshake,
not to model identity.** Every temptation this file rejects below is a temptation
to build the second thing.

### 3. Grants live in KV; D1 stays the projection

D1 holds the tabular projection and is reconciled to the bundle on every import —
`import.sh` drops what `served-tables.txt` no longer names, and only the `wh_`
prefix is protected. Authorization codes and tokens rotate and expire; that is
KV's shape, and giving them a table would put mutable session state inside the
thing whose defining property is that it is rebuilt from the bundle.

It also keeps ADR-0006 legible. `wh_audit` and `wh_callers` already mean the
surface writes *bookkeeping*; the claim that survives is that it never writes
anything a human wrote. Session state belongs in the same category as the audit
log — and out of the same store as the corpus.

### 4. The blast-radius controls do not move, and the log learns the door

Caps, tool surface, grades, audit: identical whichever door the caller came
through. A tool call is a tool call.

One addition: `wh_audit` and `wh_callers` record **which door** — header or
grant, and which client id for a grant. ADR-0010 extended the log to answer *by
whom*; two doors make that answer ambiguous unless the log says which one opened.

### 5. Fail closed on the credential, open on the feature

ADR-0020 drew this distinction for the tool list and it applies here with the
signs in different places:

- **The feature fails open into today's behaviour.** No KV binding means no
  OAuth endpoints, no discovery document, and a `401` with no
  `WWW-Authenticate` — which is precisely the current surface. An instance that
  has not opted in must not become one that advertises a door it cannot open.
- **The credential fails closed, unchanged.** No valid header and no valid
  grant is a `401`, logged as denied, exactly as now. A malformed, expired or
  revoked token is not a partial success.

An enabled instance returns `WWW-Authenticate` on the `401` and serves
`/.well-known/oauth-protected-resource` and
`/.well-known/oauth-authorization-server`, because that chain is how a
conforming client discovers where to go and there is no point implementing the
protocol while withholding the part that makes it findable.

### 6. Pre-registered clients are the documented path; DCR is opt-in

Claude's dialog has fields for a client ID and secret, so the documented setup
is: the owner mints a pair, pastes them in, done. Dynamic client registration is
supported but **off unless enabled**, because an open `/register` on a personal
record is an unauthenticated write endpoint that exists to be found by scanners.

The asymmetry is deliberate. DCR is a convenience for ecosystems with many
clients the server has never met. This server expects to meet two or three, once
each, at a keyboard.

## Alternatives rejected

- **Wait for Claude's request-header beta.** Covered above: one vendor, one row
  of the table, no timeline we control, and nothing an adopter of the public
  engine can act on.
- **Token in the URL or a path segment.** ADR-0007 rejected this on its own
  terms and nothing has changed. Not revisited.
- **Support only the `client_credentials` grant.** It would make Claude's two
  dialog fields sufficient for almost no code, and nothing would use it — MCP
  clients run authorization-code with PKCE and open a browser. Implementing a
  grant no client requests is implementing nothing.
- **A separate OAuth proxy worker in front of this one.** Identical code, one
  more hop, one more deploy, and it splits `wh_audit` across two services so the
  log stops being the single place the owner looks. The thing ADR-0010 bought
  was one record of who called; this would trade it for tidiness.
- **Multi-tenant OAuth with real accounts.** Modelling a fact that is not true
  (§2), and it turns a login gate into an identity system with everything that
  implies for storage, recovery and the corpus's own privacy claims.

## Consequences

- **Rotating `AUTH_TOKEN` must also invalidate outstanding grants.** This is the
  sharpest consequence in the file. Today rotation is total: change the secret,
  every caller is out until re-keyed. With grants in KV, a rotation that does not
  clear them leaves working access behind a secret that was deliberately retired
  — rotation would quietly stop meaning what it has meant. Whatever ships must
  clear the grant store as part of rotation, and say so in the runbook.
- **A credential-entry page now exists on the open internet**, in front of a
  personal corpus. Rate limiting, which ADR-0007 explicitly refused as a primary
  control, becomes genuinely necessary *here* — not against exfiltration, where
  the objection stands, but against guessing, which is a different threat with a
  different answer.
- **Expiry, refresh and revocation become maintenance** the surface has never
  had. Every one of them is a way for a caller to be broken by time rather than
  by a decision.
- **The worker takes its first runtime dependency.** `package.json` currently
  lists `wrangler` and nothing else; `@cloudflare/workers-oauth-provider` would
  be code we ship and do not read. Weigh that honestly against writing ~150
  lines of OAuth by hand — a protocol where the subtle mistakes are known, named,
  and already handled in the library.
- **`docs/` grows a setup path with a fork in it.** The Poke instructions stay a
  three-field form. The OAuth instructions are a KV namespace, a client pair, and
  a consent screen — and a reader must be told which one they need before they
  read either.

**What this does not settle:** whether a grant should ever be held by anyone but
the owner. Everything above assumes one person, at a keyboard, connecting their
own clients. Sharing a read of this record with another human is a different
decision, and one the exposure grades of ADR-0019 — not the login page — would
have to carry.
