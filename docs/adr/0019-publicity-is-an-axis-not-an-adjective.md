# ADR-0019 — Publicity is an axis, not an adjective

Status: proposed · 2026-08-21

## Context

`serve-manifest.json` answers one question per zone: may a remote reader see
this. Everything downstream treats that answer as uniform, and it is not.

CONTEXT already says so, about one zone:

> **already public** — A blog is the one zone where *serve is not a judgement
> about exposure*: every row is readable on the open internet today, so the
> projection reveals nothing. That changes what a good answer looks like rather
> than what may be published — the right response is the **link**, not a
> paraphrase.

That is a real distinction and it exists nowhere in the code. It surfaces once,
as a string, in the one place someone needed it:

```js
onClip: (r) => `body truncated to ${kept} of ${total} chars — the whole post is public at ${r.url}`
```

The `posts` tool knows that clipping a post costs nothing. Nothing else can act
on what it knows, because there is no field to act on.

So `serve` is answering two questions with one word:

1. May a remote reader see this row?
2. Does a remote reader seeing **all** of these rows cost anything?

For `t1_notes` the answers differ. For `t1_post` they do not.

### What that costs, concretely

ADR-0007's response caps are a **blast radius** control, and blast radius is what
an injected read takes *that it could not otherwise get*. For the blog that
quantity is zero — an attacker who wants it fetches the sitemap. The cap there
buys nothing and costs the owner every question whose honest answer is more than
twenty rows.

It also costs in the other direction. `t1_draft` — writing deliberately not
published yet — is served today under the same twenty rows as a scrobble. The
axis that would say drafts are the tightest thing on the surface does not exist,
so they get the average.

And it makes a whole tool unanswerable. `backlog` reads two piles: `read` and
`resume` come off a Goodreads shelf, `make` and `buy` off private Raindrop
collections. One tool, two exposures. Any per-*tool* policy is wrong for one
half of it.

### The two caps are not the same control

`MAX_ROWS = 20` and `MAX_BYTES = 16384` were introduced in one sentence and do
different jobs. The row cap bounds **how much of the corpus** one call takes —
security. The byte cap bounds **how much of the caller's context** one answer
consumes — ergonomics, and true regardless of who may read the rows. Conflating
them is why "add pagination" reads as one change when it is two.

## Decision

**Publicity is a second, independent axis on every served zone, declared by the
instance, fail-closed to private.**

### 1. Three grades, declared in the manifest

```json
"zone_exposure": {
  "_doc": "Independent of serve/hold. Absent = private, always.",
  "t1_post": "published",
  "t1_film_review": "published",
  "t0_music": "profile",
  "t0_book": "profile"
}
```

- **`private`** (the default, and the answer for anything undeclared) — Exo is
  the only place a stranger could read this.
- **`profile`** — readable by anyone who navigates to a profile page you own, but
  nobody ever has. A Last.fm history, a Goodreads shelf, a Letterboxd account.
- **`published`** — you put it on the internet under your name, at a canonical
  URL, intending readers. A blog post, a review.

This belongs to the **instance**, not the engine, and that is the whole reason it
goes in the manifest rather than in a table in this repository. Whether a Last.fm
profile is public is a setting on one person's account; whether a repo is public
is a setting on one person's GitHub. The engine cannot know, and a default it
guessed would be a default that publishes by being forgotten — which is the thing
`serve-manifest.json` exists to prevent.

Fail-closed for the same reason serve/hold is: marking private material public is
the worst outcome available in this system, and an omission must never produce it.

### 2. A tool's exposure is the least public zone it reads

Not declared independently. Computed the way a procedure's serve status is
computed (ADR-0016): the AND of everything it touches. A tool declaring anything
other than `private` names the zones it reads, and publish-time validation
asserts each of them is declared at least that public — the same check
`_procedure_problems` already performs, for the same reason.

A hand-declared exposure is a number somebody can get wrong in the unsafe
direction and nothing will notice.

This is what gives `backlog` its answer: as one tool it is `private`, because one
of its piles is. Split per kind, `read` and `resume` are `profile` and `make` and
`buy` stay `private`.

### 3. The row cap follows the axis; the byte cap does not

| grade | row cap | offset | audit |
|---|---|---|---|
| `private` | 20 (ADR-0007, unchanged) | never | yes |
| `profile` | raised | allowed | yes |
| `published` | raised | allowed | yes |

`MAX_BYTES` stays at 16KB for every grade. It protects the caller's context
window, which does not care who may read the rows.

**Raised, never removed, and never unaudited** — see the first consequence.

### 4. ADR-0007 §3 is amended, not loosened

It currently reads:

> Fixed semantic tools only — no raw SQL, no id-lookup loop, no pagination cursor
> that can walk the full set.

Amended: *…no pagination cursor that can walk the full set **of material that is
not already public**.* The threat model is unchanged; it is stated precisely
about the material it was always about.

### 5. The grade reaches the answer

It rides the publication receipt into the bundle and out through the response
metadata, so a caller can tell a linkable fact from an unrepeatable one. CONTEXT
already says the right answer about a post is the link rather than a paraphrase;
today that rule lives in a document no caller reads. A `published` row can carry
its URL and be quoted freely. A `private` row is the owner's own material handed
back to them, and an assistant that cannot tell the difference will eventually
paraphrase the second to somebody who asked about the first.

This is the part worth the most, and it has nothing to do with pagination.

## Consequences

- **Already-public is not already-collected, and this is the load-bearing
  caveat.** A Letterboxd account is public, but nobody has pulled all of it and
  joined it to a Goodreads shelf, a commit history and a set of timestamps. Exo
  makes that one call. Publicity lowers the blast radius of a row; it does not
  lower the blast radius of the *join*. So `profile` and `published` get a raised
  ceiling rather than none, and every grade stays in `wh_audit`.
- **`t1_draft` gets tighter, not looser.** The first thing this axis says is that
  the most sensitive zone on the surface has been sharing a cap with scrobbles.
  That is a finding, not a side effect.
- **A grade is a claim about the outside world, and the outside world changes.**
  Setting a Letterboxd account to private does not notify this repository. The
  manifest is the only place that claim lives, so it is a line to re-read when
  the account changes, and it should say so in its own `_doc`.
- **`profile` is the grade most likely to be wrong.** `published` is verifiable —
  the row carries a URL that either resolves or does not. `profile` rests on a
  setting nobody re-checks. An instance unsure about a zone should leave it
  private; the cost is twenty rows, and the cost of the other error is the
  corpus.
- **The empty cells are informative, like ADR-0015's.** A zone nobody has graded
  is a zone nobody has thought about, and it stays private until somebody does.
- **What this does not do:** it does not make anything publishable that was not.
  Publicity is orthogonal to serve/hold and is read only *after* the projection
  has already decided a row may leave. A held zone has no grade, because it has
  no rows on the surface to grade.
