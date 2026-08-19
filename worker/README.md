# The read surface

MCP over HTTP, on Cloudflare Workers. Read-only, permanently (ADR-0006). It
serves `zones/_serve/cf/` — the publication bundle, which is a physically
separate copy of the record. Held material is *absent* from it, not filtered at
read time, so nothing downstream of here can reach what you decided to hold.

This is what makes the record reachable from a phone: point an assistant at it
and it can answer from your own notes, ratings, saves and repos without that
assistant's vendor ever holding them.

- `src/index.js` — JSON-RPC, auth, the brief resource, the audit and caller logs
- `src/tools.js` — the 28 tools, and the caps
- `src/search.js` — vector search over `vectors.f32` from R2

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

## Publish data

Both legs of the nightly (`scripts/daily-sync.sh`, `.github/workflows/nightly.yml`)
do this for you. By hand:

```sh
cd "$EXO_HOME" && uv run exo publish --cf                        # builds the bundle
WRANGLER="npx wrangler" ./scripts/guard-publication.sh exo       # refuse a shrunken corpus
cd zones/_serve/cf && WRANGLER="npx wrangler" ./import.sh exo    # reconciles + loads D1
for f in vectors.f32 vectors.json brief.md; do
  npx wrangler r2 object put "exo-vectors/$f" --file "$f" --remote
done
```

(`exo` and `exo-vectors` here are the D1 database and R2 bucket you named above.)

`import.sh` reconciles: a table in D1 that is no longer in `served-tables.txt`
gets dropped, so tightening `serve-manifest.json` actually removes data. Tables
prefixed `wh_` are protected, which is where the audit log lives.

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
EXO_HOME=/path/to/your-instance node test/run.mjs    # 269 tests, D1/R2/AI stubbed
node test/verify-embedding-space.mjs https://<your-worker>.workers.dev <token>
```

`run.mjs` reads the bundle from `$EXO_HOME/zones/_serve/cf`, so publish once
before running it.

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

### What Poke can and cannot do with it

Everything here is read-only, permanently, and not by permission: there is no
write path in the surface to revoke (ADR-0006). A prompt injected into something
you saved cannot make it write, and cannot widen what it can see — the bundle
physically lacks the held material (ADR-0007). Every call is capped at 20 rows
and 16KB and lands in an audit table.

Poke also sends an `X-Poke-User-Id` header. This surface ignores it: one bundle,
one owner, one token. If you share the token, you have shared the record.

## What a client sees

**resource** `warehouse://brief` — the standing context, generated by
`warehouse/scripts_impl/brief.py` and served from R2. Hard constraints (the nut
allergy), what the owner is currently circling, how fresh each source is, and
what can be asked for. Pushed, not pulled: a pull-only surface cannot fix "I
forget things exist", because it requires already knowing what to ask.

**tools** — fixed and semantic. There is no raw SQL, no id-lookup loop and no
pagination, because the tool surface *is* the security boundary (ADR-0007).
Adding one is a decision about exposure, not a feature increment.

<!-- TOOLS:BEGIN — generated by scripts/gen-tool-table.mjs, do not edit by hand -->

| tool | domain | class | kind | answers |
|---|---|---|---|---|
| `collection` | culture | possession | entity | What the owner OWNS, which is not what they consumed: 89 vinyl records, 66 DVDs, 24 board games, 7 fragrances. |
| `reviews` | culture | authored | text | The owner's written film reviews from Letterboxd — 115 of them, in their own words, each with a link. |
| `taste` | culture | revealed | event | What the owner actually listens to, by play count — the revealed preference, as distinct from what they say. |
| `verdicts` | culture | authored | text | The owner's written opinions on books, films, tv and music — in their own words, with reasoning. |
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
| `medium` | * | lens | mixed | Everything about one medium in a single call: how much of it the owner consumes, how they rate it ON ITS OWN SCALE, what they own, and what they have written about it. |
| `ratings` | * | revealed | judgement | What the owner rated and how highly, per medium. |
| `saves` | * | intent | pointer | Links the owner bookmarked — 2,188 of them across nine years. |
| `taste_summary` | * | derived | judgement | Derived summaries of the owner's taste: how their rating scales actually behave, and the clusters their loved items fall into. |

<!-- TOOLS:END -->

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
