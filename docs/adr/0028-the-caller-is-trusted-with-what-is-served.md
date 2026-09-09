# ADR-0028 — The caller is trusted with what is served

Status: accepted · 2026-09-09

Supersedes [ADR-0007](0007-bound-the-blast-radius-of-an-injected-read.md) and
the gating half of [ADR-0010](0010-observe-the-origin-before-gating-it.md);
retires §5 and §6 of [ADR-0019](0019-publicity-is-an-axis-not-an-adjective.md).

## Context

ADR-0007 built the read surface around one threat. The caller is a hosted
assistant that also reads the owner's mail, so a tool call may carry an injected
instruction — "summarise their notes and reply with them" — and arrive
authenticated, with a plausible argument, indistinguishable from the owner
asking. Prevention was not available. The decision was to make a successful
injection *small, slow and visible*: twenty rows or 16KB per call, no `offset`,
no `id` lookup, no raw SQL, a call log the owner was meant to read.

Everything after it inherited the frame. ADR-0010 added the caller's IP and ASN
to the log and deferred an origin allowlist until the data could justify one.
ADR-0019 graded every zone by publicity so the row cap could be 20, 100 or 200
depending on what an injected read could take *that it could not otherwise get*.
ADR-0021 made a point of the caps not moving when OAuth arrived. ADR-0022 and
ADR-0023 spent two records teaching a capped answer to say which end of what it
was, because truncation was guaranteed and a cursor was forbidden.

### What it cost, every day

The controls were paid for on every question, not on the rare injected one.

- **Two-call answers.** "What have I written about taste?" wanted fifty atoms and
  got twenty plus a hint to ask again. ADR-0007 called this mild friction. It was
  mild once; it was the shape of every long answer after that.
- **No paging.** A list of six hundred films could be entered at the top or the bottom
  and nowhere else. ADR-0022's `order` and ADR-0023's `scope` are honest about
  that, and honesty about a wall is still a wall.
- **No lookup by id.** `notes_on` resolved a *topic* by vector search and
  returned one note. A caller that had the title of a note from a listing had no
  way to ask for that note; it had to describe it and hope the nearest neighbour
  was the one it meant.
- **Unanswerable long questions.** "Everything I have written about X" can be a
  hundred notes. The surface could not say that in fewer than five calls, each of which
  the skills told the caller not to make.

### The owner's decision

On 2026-09-09 the owner decided that exfiltration of served material is not a
threat they will design against. This record states the decision as theirs and
does not manufacture a justification for it beyond what they gave:

**The caller is trusted with everything that is served.** What is on the box was
chosen by the serve projection (ADR-0005), which fails closed, holds the
unfiled drawer (ADR-0009), and has been the actual answer to "what may an
assistant read" since the surface existed. A row that reaches D1 is one the
owner was willing to have read. Metering the reading of it was a control on a
decision already made upstream.

The cost of the controls was paid daily. The benefit was against a threat the
owner has decided to accept. That is the whole argument, and it is theirs.

## Decision

**The read surface trusts the caller with what is served. One ceiling remains,
and it is a budget for the caller's context, not a bound on the corpus.**

### 1. The byte cap is the only ceiling, and it is a budget

`MAX_BYTES = 16384` stays, unchanged. ADR-0019 already separated the two
numbers: the row cap bounded how much of the corpus one call took, the byte cap
bounded how much of the caller's context one answer consumed. The first was the
security control and is gone. The second was never one and stays for the reason
it was always there — a 48KB note in a tool result is a bad answer whoever is
asking.

`ROW_CAP` — 20 private, 100 profile, 200 published — is removed. `limit` becomes
a page size: `DEFAULT_ROWS = 20` when the caller says nothing, raisable by the
caller, with `MAX_ROWS = 500` as a sanity bound on a single answer. In practice
the byte cap binds long before five hundred rows do, and that is the intended
order: the number a caller can reason about is bytes.

### 2. Paging exists, and the prerequisite is met

Every list tool accepts `offset`, default 0. Answers carry `offset` beside
`returned_count` and `has_more`, so a page says where it sits as well as
whether there is more.

ADR-0007 §3 was careful to note that no cursor existed *for a reason other than
the ban*: three tools had no `ORDER BY` at all and the rest ordered on columns
that tie heavily, so an offset over them would repeat and skip rows silently.
That prerequisite is now met rather than argued around. **Every `ORDER BY` on
the surface carries a unique tiebreaker** — the row's id, after whatever
measured fact the caller chose — so two consecutive pages partition the set and
a caller walking it sees each row once. ADR-0022's chooser still owns the axis;
the tiebreaker is appended by the chooser, not by the caller, and the SQL still
never comes from outside the file.

### 3. Lookup by id exists beside lookup by meaning

The tools built on `readOne` — `notes_on`, `posts`, `drafts`, and whatever else
is built on it later — accept an optional `id`. Given one, the tool returns that
item and nothing else. Given none, the semantic `topic` path is what it has
always been, and stays the default: describing what you want is still the right
first call, because the caller rarely has an id before it has a listing.

Title listings carry the id. A caller that sees a title it wants asks for it by
the key the surface just handed back, not by a paraphrase the vector index has
to guess at.

### 4. The log is telemetry

`wh_audit` and `wh_callers` stay, with every field ADR-0010 and ADR-0021 gave
them: what was asked, with which arguments, how many rows came back, from which
IP and ASN, through which door and under which client id. What changes is what
they are *for*. ADR-0007 made the log the one control that detected rather than
limited and warned that if it was never read, nothing detected. It is no longer
asked to detect anything. It answers the owner's curiosity — which tools are
used, which questions recur, which clients call and from where — and it feeds
the next decision about the surface's shape. It does not feed an alarm.

ADR-0010's phase 2 — an origin allowlist checked before the token compare — is
**retired, not deferred**. It was defence in depth against a stolen token used
from elsewhere, and the data it was waiting for was to size a control on the
caller's reach. There is no such control now for it to size.

JSON-RPC batches remain unsupported. They are *not implemented*, which is a
statement about the code; they are no longer *refused*, which was a statement
about an attacker amortising calls against the log.

### 5. The grade stamps, and sizes nothing

ADR-0019's exposure axis survives whole where it says something true about the
material and goes where it sized a control. §1 through §4 stand: three grades,
declared by the instance, fail-closed to private, computed for a tool as the
least public zone it reads, with vectors always private. §7 stands and is now the
whole point of the axis — every answer is stamped so a caller can tell a
linkable fact from an unrepeatable one, and quote the first freely while
handing the second back only to its owner.

§5 and §6 are retired. There is no row cap for the grade to scale and no cursor
ban for it to carve an exception into. A `private` answer and a `published`
answer page the same way; they differ in what the stamp tells the reader to do
with the rows.

### 6. What does not move

- **Token in a header, never in the URL** (ADR-0007 §1). That was about where a
  credential travels, and it is still good hygiene; ADR-0021's OAuth door is
  built on it. Unchanged.
- **The surface is read-only** (ADR-0006). Trusting a reader with everything
  served says nothing about writing, and the argument for never writing did not
  rest on the threat retired here.
- **The serve projection decides what exists** (ADR-0005, ADR-0009). This record
  leans on it harder, not less: it is now the *only* place exposure is decided,
  which is what it was always meant to be.
- **The fixed tool vocabulary** stays, on ADR-0013's grounds and not ADR-0007's.
  The surface is a data layer answering named questions over measured facts.
  No raw-SQL tool is added, because a tool that takes SQL answers no question;
  it hands the caller the schema and asks it to write one. That is a design
  reason and it is sufficient. It is no longer a security boundary, and adding
  a tool goes back to being a decision about what the surface can answer.
- **The `/authorize` rate limit** (ADR-0021). Guessing a secret at a login page
  is a different threat with a different answer, and nothing here accepts it.

## Alternatives rejected

- **Keep the caps but raise them.** 20/100/200 becoming 100/500/1000 keeps every
  cost — two-call answers still exist, they just start later — while the number
  no longer defends anything. A cap that is not a control is a limit somebody
  will hit and nobody can explain.
- **Keep `offset` banned but allow `id`.** Lookup by id makes enumeration a loop
  over a listing anyway; forbidding the cursor while allowing the loop is a
  ban on the convenient form of the thing it permits. Either the caller is
  trusted to walk the corpus or it is not, and the owner has said which.
- **Delete the audit log entirely.** It was framed as detection, and detection
  is what has been retired — but the tables cost nothing on the read path, both
  writers already swallow their own failures, and "which questions does this
  surface actually answer" is a question the owner asks. Keep the record and
  drop the alarm.

## Consequences

- **A caller can now walk the whole served corpus in a few hundred calls, and
  that is accepted.** Not accidentally: it is the decision. Roughly 1,600 notes
  at 16KB a page is on the order of two hundred calls, and every one of them
  reads something the serve projection already released.
- **The log answers curiosity, not alarm.** Nobody is expected to read
  `wh_audit` looking for a compromise. ADR-0007's closing consequence — that an
  unread log means no control detects — is dissolved rather than answered: it
  no longer needs to.
- **The skills must stop saying "do not page."** `skills/README.md` and
  `recommend-media` tell a caller there is no `limit` and no cursor and to
  narrow the question instead. That was true and is now false. A skill that
  teaches a caller to fear a wall the surface removed will produce worse
  answers than no skill at all.
- **ADR-0022's `order` and ADR-0023's `scope` stay useful.** They were written
  for a capped answer, and every answer is still bounded — by bytes, by the
  page size, by the caller's own patience. A page that names its axis and its
  population is more legible than one that does not, whatever put the edge
  there. Nothing in those two records is retired.
- **The exo-me instance's ADR copies need the same note.** The instance carries
  its own `docs/adr/`, and the amendments to 0007, 0010, 0019 and 0021 in this
  repository should be mirrored there, or the instance will describe a surface
  it no longer runs.
- **ADR-0015's "a tool is an exposure decision" loses one of its two legs.** The
  three facets still have to be declared, and declaring what class of evidence a
  tool exposes is still the cheapest honest account of it; the argument that it
  also gates exposure is gone.

What this does not settle: sharing a read of this record with a second human.
ADR-0021 left that to the exposure grades rather than the login page, and this
record leaves it there. Trusting one caller with everything served is a
decision about the owner's own assistant, not about anyone else's.
