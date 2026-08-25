# The read surface

MCP over HTTP, on Cloudflare Workers. Read-only, permanently (ADR-0006). It
serves `zones/_serve/cf/` — the publication bundle, which is a physically
separate copy of the record. Held material is *absent* from it, not filtered at
read time, so nothing downstream of here can reach what you decided to hold.

This is what makes the record reachable from a phone: point an assistant at it
and it can answer from your own notes, ratings, saves and repos without that
assistant's vendor ever holding them.

- `src/index.js` — JSON-RPC, auth, the brief and procedure resources, the audit and caller logs
- `src/tools.js` — the 31 tools, and the caps
- `src/search.js` — vector search over `vectors.f32` from R2
- `src/exposure.js` — how public each zone is (ADR-0019), and what that makes a tool
- `src/surface.js` — which tools this instance offers (ADR-0020)

`exposure.json` rides along beside the brief: how public each served zone is
(ADR-0019), resolved by `exo publish` so nothing out here re-decides it. Every
tool declares the zones whose content can reach a caller, and its grade is the
least public of them — so a tool touching one private zone is private whatever
else it reads.

A tool that spans two grades is graded on what each CALL reads, not on
everything it could: `backlog` is `profile` for a Goodreads shelf and `private`
for a Raindrop collection, from the same tool. That narrowing is the only
direction available — a test asserts the per-call set is always a subset of the
declared one, so it can never grade a call more public than the tool says.

The row cap follows that grade — 20 private, 100 profile, 200 published — and the
byte cap does not, because 16KB protects the caller's context window rather than
the corpus. Every tool takes an optional `limit`, which only ever narrows.
`tools/list` is built per request so the ceiling a model is told about is the
real one.

**There is no `offset`, and passing one is an error rather than a no-op.** A
cursor that can walk the full set is the one thing the tool surface is not
(ADR-0007 §3). Answers carry `has_more`; the way to see more is a narrower
question.

Every answer also carries `exposure` itself, which says whether the rows may be
quoted onward or are the owner's own material handed back to them. A missing or
unreadable `exposure.json` grades everything private, so a worker deployed ahead
of its bundle serves tight rather than open.

`surface.json` rides along the same way, and answers a different question: not
how public a tool's answer is, but whether this instance offers the tool at all.
An instance drops one for either of two reasons — a dependency it holds, or a
peer server that answers better (ADR-0020) — and `exo publish` resolves both
into a list of names so nothing out here re-decides it.

It fails in the OPPOSITE direction to `exposure.json`, deliberately. A missing
tool list means every tool the engine defines, because this file cannot widen
what leaves: a held zone is absent from the projection, so a tool reaching for
it finds nothing whether or not it was advertised. Failing closed here would
instead hide every newly-shipped tool until each instance named it — the
"publishing is not offering" failure ADR-0013 called a standing duty.

Hand-rolled JSON-RPC, no MCP SDK: the surface used here is five methods, and a
dependency-free Worker is one less thing that can change under a corpus this
personal.

## Setup

You need a Cloudflare account with Workers, D1 and R2 enabled. R2 needs turning
on in the dashboard once; the rest is CLI.

Copy `wrangler.example.toml` to `wrangler.toml` first — it is gitignored,
because it holds *your* database id and bucket names, which belong to your
instance rather than to this engine.

```sh
cp wrangler.example.toml wrangler.toml

npx wrangler d1 create exo                  # put the id it prints into wrangler.toml
npx wrangler r2 bucket create exo-vectors   # and the bucket name
npx wrangler deploy                         # the Worker must EXIST before a secret can be set
npx wrangler secret put AUTH_TOKEN          # openssl rand -hex 32
```

A Worker with no `AUTH_TOKEN` rejects every request, so deploying before the
secret exists is safe — it serves nothing rather than serving openly. Set the
`[vars]` in `wrangler.toml` too: without `BLOG_URL_TEMPLATE` a post hit carries
no link, which is most of the point of that zone.

That is the whole surface: one secret, one header, no store. If you also want
hosted assistants that cannot send a custom header — claude.ai, ChatGPT — see
[the second door](#the-second-door-oauth) below. It is optional and additive;
skipping it changes nothing about the above.

## Publish data

Both legs of the nightly (`scripts/daily-sync.sh`, `.github/workflows/nightly.yml`)
do this for you. By hand:

```sh
cd "$EXO_HOME" && uv run exo publish --cf                        # builds the bundle
WRANGLER="npx wrangler" ./scripts/guard-publication.sh exo       # refuse a shrunken corpus
cd zones/_serve/cf && WRANGLER="npx wrangler" ./import.sh exo    # reconciles + loads D1
for f in vectors.f32 vectors.json brief.md exposure.json surface.json; do
  npx wrangler r2 object put "exo-vectors/$f" --file "$f" --remote
done
```

(`exo` and `exo-vectors` here are the D1 database and R2 bucket you named above.)

`import.sh` reconciles: a table in D1 that is no longer in `served-tables.txt`
gets dropped, so tightening `serve-manifest.json` actually removes data. Tables
prefixed `wh_` are protected, which is where the audit log lives.

Import before deploy, in that order, whenever the worker learns about a new
table. `resources/list` queries `t1_procedure` directly, so a worker deployed
ahead of its bundle answers nothing at all for the brief either.

## Deploy

```sh
npm run deploy
```

An instance that keeps its `wrangler.toml` outside this checkout — which is what
the engine/instance split implies — passes it explicitly instead:

```sh
npx wrangler deploy --config /path/to/your-instance/worker/wrangler.toml
```

`main` in that file is resolved relative to the file itself, so point it at this
repo's `src/index.js`.

## Test

```sh
EXO_HOME=/path/to/your-instance node test/run.mjs      # D1/R2/AI stubbed
EXO_HOME=/path/to/your-instance node test/sqlcheck.mjs # every query, against the real schema
node test/verify-embedding-space.mjs https://<your-worker>.workers.dev <token>
```

`sqlcheck.mjs` prepares every statement the tools can emit against the published
`schema.sql`. SQLite validates table and column references at prepare time, which
catches the one class of mistake a stub returning `{rows: []}` cannot: a clause
naming a column that is not in scope parses fine as a string and fails only when
somebody asks that question. Zones a bundle happens not to carry are shimmed, so
an incomplete fixture cannot quietly skip the most intricate SQL on the surface.

`run.mjs` reads the bundle from `$EXO_HOME/zones/_serve/cf`, so publish once
before running it.

The second door has its own end-to-end run, against a server rather than a stub,
because almost none of it is our code — the point is that PKCE, single-use codes,
refresh and audience binding behave as the spec says against *this*
configuration:

```sh
npx wrangler dev --local-protocol https --var AUTH_TOKEN:devtoken --var OAUTH_ALLOW_DCR:true
node test/oauth-flow.mjs https://127.0.0.1:8788 devtoken
```

Both flags are load-bearing. The provider refuses a non-HTTPS issuer, and the
flow needs a client to exist, which in a test means dynamic registration — off
in production for the reason below.

The corpus is embedded by Python bge-small; queries by Workers AI's copy of it.
Choosing Cloudflare rests on those being one vector space, and `run.mjs` cannot
cover it because it stubs the AI binding.

**Measured 2026-08-19: they are not identical.** The same text through both gives
cosine 0.9031, not ~1.0 — a pooling or normalisation difference. It does not
matter: the offset is systematic, so ordering survives (the source atom still
ranked #1 of 8,446, at 0.9031 vs 0.6464 for second). Which is why the verifier
tests rank, and why its absolute threshold sits at 0.75.

## Connect it to Poke

[Poke](https://poke.com) is an assistant you talk to over iMessage, and it is
the reason this surface exists: it puts your own record behind a text message,
without the assistant's vendor holding any of it.

1. Deploy the Worker and load a bundle, as above. Check it is alive:

   ```sh
   curl https://<your-worker>.workers.dev/          # -> "exo read surface"
   curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<your-worker>.workers.dev/ \
     -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
                                                    # -> 401, because no token was sent
   ```

   A 401 there is the good answer. A 200 means `AUTH_TOKEN` is unset and the
   surface is open.

2. Go to [poke.com/integrations/new](https://poke.com/integrations/new) (also
   reachable from *Settings → Connections*) and fill in three fields:

   | field | value |
   |---|---|
   | Name | anything — "Exo" |
   | MCP Server URL | your Worker's root URL, `https://<your-worker>.workers.dev` |
   | API Key | the `AUTH_TOKEN` you generated |

   No `/sse` path and no trailing segment: this server speaks JSON-RPC over
   POST at the root, and it answers on any path.

3. Poke connects and discovers the tools. It sends the key as
   `Authorization: Bearer <key>`, which is the header this server prefers —
   though it also accepts `x-api-key`, `api-key` and `x-auth-token`, because
   clients disagree about where an "API key" belongs and an opaque 401 cannot
   tell a wrong key from a wrong header.

4. Ask it something only your record knows. Good first messages, because they
   exercise different halves of the surface:

   - *"read my exo brief"* — the standing context resource, which is what a
     reader is supposed to load before anything else
   - *"what have I written about attention?"* — vector search over notes and
     the spans lifted from them
   - *"what am I in the middle of writing?"* — drafts, the zone most likely to
     be empty on a new instance

### If it does not work

- **Every call 401s.** The key is wrong, or `AUTH_TOKEN` was never set. Set it
  with `npx wrangler secret put AUTH_TOKEN` and re-enter it in Poke — a secret
  changed in Cloudflare does not propagate to a client that stored the old one.
- **Poke connects but finds no tools.** It reached the Worker but got no
  `tools/list` result — usually the URL points at a path that returns the GET
  banner rather than accepting POST. Use the root URL.
- **Tools exist but every answer is empty.** The Worker is up and D1 is not
  loaded. Run the publish steps above and check `import.sh` printed
  `every table matches the bundle`.
- **Answers are stale.** The surface serves the last bundle you loaded, and
  says so: the brief carries its own age. Re-run the publish steps.
- **You changed the tool descriptions and Poke still uses the old ones.**
  Re-sync the tool list from Poke's integrations page; discovery is cached.

## The second door (OAuth)

Bearer-in-a-header reaches every client that lets you set one — Poke, Claude
Code, curl. It reaches none of the hosted assistants, whose connector dialogs
offer OAuth and nothing else. ADR-0021 has the argument; this is the setup.

```sh
npx wrangler kv namespace create OAUTH_KV     # put the id in wrangler.toml
npx wrangler d1 execute exo --remote --file migrations/0002-which-door.sql
npx wrangler deploy
```

Then uncomment `[[kv_namespaces]]` and `compatibility_flags` in `wrangler.toml`.
The binding's presence *is* the switch: without it the Worker has no
`/authorize`, no discovery documents and no `/mcp`, and is byte-for-byte the
surface described above.

What you get:

| path | who uses it |
|---|---|
| `/` | the header door — Poke, Claude Code. Unchanged, forever. |
| `/mcp` | the same tools behind a grant — claude.ai, ChatGPT |
| `/authorize` | the consent screen, which asks for your `AUTH_TOKEN` |
| `/oauth/token` | issue and refresh |
| `/.well-known/oauth-protected-resource/mcp` | RFC 9728 |
| `/.well-known/oauth-authorization-server` | RFC 8414 |

Two paths rather than one because RFC 9728 pins the `resource` identifier to the
URL the user typed, and the URL Poke has already typed is the root. Giving the
grant door its own path is what lets the old one stay exactly as it was.

**In claude.ai:** *Customize → Connectors → Add custom connector*, and enter the
`/mcp` URL — `https://<your-worker>.workers.dev/mcp`, the path included. Leave
the OAuth client fields empty. Claude identifies itself with a Client ID
Metadata Document, so there is no client id to mint and no open registration
endpoint to expose. You will get a login screen; the password is your
`AUTH_TOKEN`.

There is one person behind this record, so there are no accounts here. The
consent screen gates on the same secret the header door uses, and the access
token it issues is simply a different string with a lifetime.

### Rotating the token

```sh
openssl rand -hex 32 | npx wrangler secret put AUTH_TOKEN
```

**With the second door enabled, rotation is no longer complete on its own.** A
grant outlives the secret that authorised it, so a rotation that leaves the
namespace populated retires the password and not the access. Empty it too:

```sh
npx wrangler kv key list --binding OAUTH_KV \
  | jq -r '.[].name' \
  | while read -r k; do npx wrangler kv key delete --binding OAUTH_KV "$k"; done
```

Every connected client then reconnects: Poke and Claude Code with the new token,
anything on `/mcp` by logging in again.

### If it does not work

- **"Couldn't reach the MCP server" and your Worker sees nothing.** Discovery
  failed before the first call. Check
  `curl https://<your-worker>.workers.dev/.well-known/oauth-protected-resource/mcp`
  returns JSON whose `resource` matches the URL you typed **exactly**, path
  included.
- **It connects, then every call 401s.** The token is bound to the resource it
  was issued for. A grant minted against `/mcp` will not open the root, and a
  header token will not open `/mcp` — they are different credentials and the
  test asserts they stay that way.
- **Claude tries dynamic registration and gets a 404.** It fell back from CIMD,
  which means the metadata is not advertising it. That needs
  `compatibility_flags = ["global_fetch_strictly_public"]`; without the flag the
  provider reports `client_id_metadata_document_supported: false` and Claude
  never tries it.
- **The consent screen refuses a token you are sure is right.** Ten attempts per
  IP per minute, then a 429. Wait a minute. Refusals land in `wh_callers` as
  `denied:authorize`, which is the one outcome there that means guessing rather
  than misconfiguration.

### What Poke can and cannot do with it

Everything here is read-only, permanently, and not by permission: there is no
write path in the surface to revoke (ADR-0006). A prompt injected into something
you saved cannot make it write, and cannot widen what it can see — the bundle
physically lacks the held material (ADR-0007). Every call is capped at 20 rows
and 16KB and lands in an audit table.

Poke also sends an `X-Poke-User-Id` header. This surface ignores it: one bundle,
one owner, one token. If you share the token, you have shared the record.

## What a client sees

### Resources

**`exo://brief`** — the standing context, generated by `exo/scripts_impl/brief.py`
and served from R2. Who the owner is, what they are currently circling, how fresh
each source is, and what can be asked for. Pushed, not pulled: a pull-only
surface cannot fix "I forget things exist", because it requires already knowing
what to ask. (`warehouse://brief` still resolves — renaming a published address
is a migration, not an edit.)

**`exo://procedure/<slug>`** — one hand-written procedure: how the owner does a
recurring thing. A resource rather than a tool, because a tool is a named
question whose answer depends on the record and a procedure is a document whose
content does not (ADR-0016). `resources/list` is the index, queried live from
`t1_procedure`, so **the list is whatever your instance has written** — the
table below cannot be generated here the way the tool table is, because
procedures are instance data rather than engine code.

This is the only place the surface returns text meant to be **acted on**, which
is why it is also the most tightly bounded:

- hand-authored only. No loader path, nothing derived, nothing promoted from
  captures. Never embedded, so procedure text cannot surface under
  `whats_relevant`, which promises the owner's prose.
- a read takes a URI and nothing else. `^exo://procedure/[a-z0-9-]+$`, matched
  strictly — no traversal, no wildcard. An unknown slug returns `-32602`, the
  same answer a held one gives, because a held procedure is absent from the
  bundle rather than filtered here.
- the body is never returned raw. The worker frames it: whose document this is,
  the blocking preconditions **above** the steps, how old the owner's last
  verification is, and — for a `kind: action` procedure — that the sink and
  target are fixed by the author and that text returned by exo tools may fill a
  payload but never choose a target.
- a served procedure that reads a held zone **fails the publish**. The build,
  not the read: naming a tool for a held zone publishes a map of what is being
  held, and the tool returning nothing does not unsay it.

Writing one is a file, not a deploy: drop it in `$EXO_HOME/procedures/`, publish,
import. The resource list picks it up.

**Deploy order matters.** `resources/list` queries `t1_procedure` and is not
wrapped in try/catch, so import the D1 bundle **before** deploying a worker that
knows about procedures — a missing table takes the list down for the brief too.
That is deliberate: a list that silently degraded to the brief alone would hide a
broken deploy until someone happened to ask for a procedure.


### Tools

Fixed and semantic. There is no raw SQL, no id-lookup loop and no
pagination, because the tool surface *is* the security boundary (ADR-0007).
Adding one is a decision about exposure, not a feature increment.

<!-- TOOLS:BEGIN — generated by scripts/gen-tool-table.mjs, do not edit by hand -->

| tool | domain | class | kind | answers |
|---|---|---|---|---|
| `collection` | culture | possession | entity | What the owner OWNS, which is not what they consumed: 89 vinyl records, 66 DVDs, 24 board games, 7 fragrances. |
| `criticism` | culture | world | text | What the music press is publishing — titles, bylines, dates and the outlet's own blurb, from the underground outlets the owner follows, with the link to the piece. |
| `releases` | culture | world | entity | Records that came out lately in the scenes the owner listens to, with what they have NOT already heard removed. |
| `reviews` | culture | authored | text | The owner's written film reviews from Letterboxd — 115 of them, in their own words, each with a link. |
| `taste` | culture | revealed | event | What the owner actually listens to, straight off the scrobble stream — revealed preference, as distinct from what they say. |
| `verdicts` | culture | authored | text | The owner's written opinions on books, films, tv and music — in their own words, with reasoning. |
| `watching` | culture | revealed | entity | What the owner started and has not finished, per show: episodes watched, how long since the last one, and an episode total where one is known. |
| `places` | table | authored | entity | Restaurants the owner has been to, with their own notes and ratings. |
| `recipes` | table | authored | text | What the owner actually cooks — recipes they wrote up and published, with their source links. |
| `drafts` | mind | authored | text | Longform pieces the owner is in the middle of writing — the state between a private note and a published post. |
| `notes_on` | mind | authored | text | The owner's notes about a topic. |
| `open_threads` | mind | intent | pointer | Questions the owner has asked themselves and not closed. |
| `posts` | mind | authored | text | The owner's published blog — articles, essays, lists, project write-ups — as opposed to the private notes behind them. |
| `recent_topics` | mind | dialogue | event | What the owner has been working through in conversation lately — titles and volume, not transcripts. |
| `thread` | mind | dialogue | text | One conversation. |
| `whats_relevant` | mind | derived | vector | What has the owner written that bears on a topic? |
| `project_activity` | workshop | revealed | event | What the owner actually worked on, dated — commit subjects from their own repos. |
| `project_docs` | workshop | authored | text | The prose those repos carry: READMEs, CONTEXT glossaries, architecture decision records and plan documents. |
| `project_open` | workshop | intent | pointer | What is visibly unfinished in those repos: TODO and FIXME markers left in code, unchecked items in plan documents, and files sitting uncommitted. |
| `projects` | workshop | possession | entity | The owner's repos — what they are building, what they set down, and what each one claims to be. |
| `agenda` | commitments | intent | pointer | What the owner has committed to and where it stands — the item spine Kairos schedules from. |
| `history` | commitments | revealed | event | What actually happened to the owner's commitments — the append-only log behind the item spine. |
| `events` | world | world | entity | Upcoming DC events the owner could actually go to — a live pool merged from eight sources (library, theatre, improv, cinema, parties, music venues). |
| `taste_profile` | world | authored | judgement | What the owner SAYS they like — stated preferences: venues and orgs they rate, things they seek out, things they avoid. |
| `around_the_time` | * | lens | mixed | What was going on around a period: what the owner wrote, listened to, read or watched. |
| `backlog` | * | intent | pointer | What the owner has queued but not done — things they decided they wanted and have not gotten to. |
| `consumption` | * | revealed | event | Shape and recency of what the owner consumes, per medium: how much, and how current the record is. |
| `facets` | * | revealed | judgement | How the owner rates a medium BROKEN DOWN by a facet of the thing itself — for beer: by style family, by full style, by brewery, by venue, or by the beer. |
| `medium` | * | lens | mixed | Everything about one medium in a single call: how much of it the owner consumes, how they rate it ON ITS OWN SCALE, what they own, and what they have written about it. |
| `ratings` | * | revealed | judgement | What the owner rated and how highly, per medium. |
| `saves` | * | intent | pointer | Links the owner bookmarked — 2,188 of them across nine years. |
| `taste_summary` | * | derived | judgement | Derived summaries of the owner's taste: how their rating scales actually behave, and the clusters their loved items fall into. |

<!-- TOOLS:END -->

**Ordering is part of the contract (ADR-0022).** Every `ORDER BY` is over a
measured fact, and where a tool's rows carry more than one, the caller picks
which orders the answer — from one vocabulary, so it is learned once:

| `order` | what it means | offered by |
|---|---|---|
| `recent` | newest first — the default wherever the record carries a date | `ratings`, `reviews`, `places`, `collection`, `backlog`, `projects`, `taste`, `facets` |
| `oldest` | the same axis reversed, for what has been sitting | `backlog`, `collection`, `taste` |
| `rated` | the owner's own rating, highest first | `ratings` (default), `reviews`, `places` (default), `facets` |
| `played` | how often they actually reached for it | `collection`, `taste` (default), `facets` (default) |

A tool that offers a choice returns `order: "<name>"` beside `returned_count`
and `has_more`, because a capped list means something different on each axis:
twenty films off 720 sorted by rating are the *top* twenty, and twenty by
recency are the *last* twenty. A tool with one axis says nothing, because there
is nothing a caller could have asked for instead. An unrecognised name falls back to the default and says so rather than
failing the call. `verdicts` offers no `recent`, because that zone carries no
date and a sort over an all-NULL column is hash order wearing a promise.

**And a slice says what it is a slice of (ADR-0023).** Naming the axis fixed
half the problem: it says which end of a list twenty rows came from, and it
still cannot say twenty *of what*. Where a tool's rows are drawn from a
population the caller cannot see — every artist in the scrobble stream, every
medium inside a date window — the answer carries `scope`, which counts that
population in a sentence:

```
taste            "40,561 plays by 2,314 artists, 2016-04-02 → 2026-06-02"
around_the_time  "2026-03-01 → 2026-03-31 held 12 notes · 61 artists over 704
                  plays · 9 films · 2 books; these rows are the head of each"
```

Two more rules fall out of the same failure. A tool is graded on the record it
is *about*, so an optional column drawn from a more private zone is opt-in and
costs its grade only on the call that asks for it — `taste(with_mentions:true)`
reads the notes and answers twenty; `taste` alone reads the scrobbles and
answers a hundred. And a field whose values are a closed hand-kept vocabulary
returns that vocabulary, because a search that misses one of its buckets is a
gap in the vocabulary and not in the shelf — `collection` carries `genres` on
every answer for exactly that reason.

The four project tools carry prose and metadata only. No source code is captured
at any layer (ADR-0011), so a reader offering to review that code from here is
offering something the store does not hold.

Every call is capped at **20 rows / 16KB**, whichever binds first, and logged to
`wh_audit`. The caps do not make exfiltration impossible — an authenticated
caller is indistinguishable from an injected one. They convert "one quiet call
takes everything" into "hundreds of calls, visibly", which is worth something
only if the log is read. Read it.

Batched JSON-RPC is rejected outright: a batch would let one request pull N caps'
worth and turn the per-call cap into no cap.

## Who is calling

Phase 1 of origin gating (ADR-0007): **observe now, gate later.** An allowlist
written before the answer is known locks out the client it was meant to protect,
and assistants do not publish their egress.

`Origin` is not the lever. Poke and every other assistant call this
server-to-server — no Origin header, no preflight — so anything CORS-shaped
would be decoration. What exists is the IP and the ASN, which Cloudflare fills
in `req.cf` before the isolate runs.

Two tables, both `wh_`-prefixed so `import.sh` will not reconcile them away:

- **`wh_callers`** — where calls come from, rolled up per day per
  (ip, asn, ua, outcome). `outcome='ok'` is the allowlist candidate;
  `outcome='denied'` is someone holding a wrong token, and that is the row worth
  an alarm. Rolled up rather than appended because the question is "which
  distinct places call", and because an append-only table on a path anyone can
  reach is a billing lever. Denials are additionally throttled to one write per
  ip per minute per isolate.
- **`wh_audit`** — unchanged per-call log, now carrying `ip` and `asn`. With more
  than one token holder (the owner's own machine, plus whatever assistant is connected),
  "what was asked" without "by whom" cannot tell a normal week from a compromise.

### Deploying it

Order matters. The audit writer is wrapped in a try/catch so it can never take
the surface down — which also means a column mismatch fails **silently**: the
surface answers normally and stops logging. Migrate first.

```sh
npx wrangler d1 execute warehouse --remote --file migrations/0001-caller-observability.sql
npm run deploy
```

The ALTERs are not idempotent; re-running the file is expected to fail on them
and that failure is harmless.

### Reading it

```sh
npx wrangler d1 execute warehouse --remote --command \
  "SELECT ip, asn, org, country, ua, SUM(n) n, MIN(first_seen) first, MAX(last_seen) last
     FROM wh_callers WHERE outcome='ok' GROUP BY ip, asn, ua ORDER BY n DESC"
```

```sh
npx wrangler d1 execute warehouse --remote --command \
  "SELECT day, ip, asn, org, country, ua, n FROM wh_callers
     WHERE outcome='denied' ORDER BY day DESC, n DESC LIMIT 30"
```

What to look for before phase 2: whether the `ok` set for the assistant is a
handful of stable addresses (an IP allowlist is worth writing) or a wide spread
inside one cloud ASN (an ASN gate then admits that whole cloud, which is most of
the internet — the caps and this log stay the real control). Give it a few days
of normal use; a gate written off one afternoon's traffic is a gate that fires
at 2am.

Worth being clear about what an IP gate buys even at its best: it stops a
**stolen token used from elsewhere**. It does nothing about the threat ADR-0007
already names — injected instructions executing inside the legitimate client,
which arrive from exactly the right address.

## Auth

Bearer token in a header, never the URL. `authorization: Bearer <token>` is the
normal shape; `x-api-key`, `api-key` and `x-auth-token` are also accepted with
the same single secret, because clients disagree about where an "API key" goes
and the failure mode is an opaque 401 either way. Compared in constant time.

That is the header door, at the root, and it is not a legacy path — it is the
configuration of this surface with no store, no expiry and no redirect in it,
and an instance that wants only that should be able to have only that.

An instance with `OAUTH_KV` bound also accepts a grant at `/mcp`
([the second door](#the-second-door-oauth), ADR-0021). The two credentials are
not interchangeable in either direction: a grant issued for `/mcp` does not open
the root, and the header secret does not open `/mcp`. Authentication is not what
bounds this surface in the first place — the caps, the fixed tool list, the
exposure grades and the log are, and every one of them is indifferent to which
door a caller came through. The log is the exception, and only because it now
records which one did.
