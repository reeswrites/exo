# The checker

A cron-only Worker that decides when a lane should run. It reads one watermark
per source, compares it against a cursor in KV, and fires a GitHub
`repository_dispatch` when it moved.

**Lanes fire on change, not on a clock.** A 15-minute schedule in Actions spends
ninety-six runner startups a day to learn nothing ninety times. A 15-minute
check here costs a few API calls and starts a runner only when there is work.
That is the difference between the lane budget fitting and not (ADR-0015 §3).

**It has no `fetch` handler, and that is the design.** It holds upstream tokens,
which ADR-0015 §4 refuses to put in the read surface. The rule being kept is not
"no upstream credential may touch Cloudflare" — it is that no upstream
credential may sit behind a handler an authenticated caller can reach. A Worker
with no `fetch` export cannot serve a request at all, so there is nothing to
inject into. Adding one, for a health check or anything else, removes the only
reason this is a second Worker.

## Sources

| source | watermark | fires |
|---|---|---|
| Notion | newest `last_edited_time` from `POST /v1/search`, one page | `notes-changed` |
| Last.fm | newest scrobble `uts` (never the now-playing entry) | `consumption-changed` |
| Raindrop | newest raindrop `_id` | `consumption-changed` |

A source with no credentials configured is skipped, not failed — an instance
need not hold all of them.

## Setup

```sh
cp wrangler.example.toml wrangler.toml
npx wrangler kv namespace create CURSORS    # put the id in wrangler.toml
npx wrangler deploy
npx wrangler secret put GITHUB_TOKEN
npx wrangler secret put NOTION_TOKEN
npx wrangler secret put LASTFM_API_KEY
npx wrangler secret put RAINDROP_TOKEN
```

The GitHub token should be a fine-grained PAT scoped to the **one** instance
repository. `repository_dispatch` needs `Contents: write`; nothing else is
required, and nothing else should be granted.

## Two things it does on purpose

**The cursor is written before the dispatch.** The other order re-fires forever
if the dispatch succeeds and the KV write does not, and a lane firing every
fifteen minutes on unchanged data is worse than one that misses a change until
the next real one.

**A rotten source does not stop the others.** Nobody is reading a thrown error
in a cron handler; each source is caught, logged, and retried on the next run.
