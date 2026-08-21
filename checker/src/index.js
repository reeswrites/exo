/**
 * The checker — a cron-only Worker that answers one question per source:
 * *has anything changed since last time*, and fires a GitHub
 * `repository_dispatch` when the answer is yes.
 *
 * It exists because a lane should fire on change, not on a clock. Both of the
 * sources that matter answer the change question in a single cheap call —
 * Notion through `last_edited_time`, GitHub through a repo's `pushed_at` — and
 * so do Last.fm and Raindrop. A 15-minute *schedule* would spend ninety-six
 * runner startups a day to learn nothing ninety times; a 15-minute *check*
 * starts a runner only when there is work (ADR-0015 §3).
 *
 * It is also why this is a SEPARATE Worker from the read surface, and the
 * separation is the point rather than tidiness:
 *
 *   - There is NO `fetch` handler here. Not an empty one, not one that returns
 *     404 — none. A Worker with no fetch export cannot serve a request, so
 *     there is no request surface on it to inject into.
 *   - It holds upstream tokens, which ADR-0015 §4 refuses to put in the read
 *     surface. That refusal is not "no upstream credential may touch
 *     Cloudflare"; it is "no upstream credential may sit behind a handler an
 *     authenticated caller can reach". Same isolate, same secret store, and a
 *     bug in a request handler does not care which trigger a secret was meant
 *     for — so the two must not be the same isolate.
 *   - Every token here is READ-scoped, and the GitHub one is narrower still:
 *     `repository_dispatch` on one repository and nothing else.
 *
 * State lives in KV: one cursor per source, holding the last value seen. A
 * cursor that fails to write means the next run re-fires — the lane is
 * idempotent, so a duplicate costs a rebuild, and a missed one costs freshness
 * until the next check. Erring toward re-firing is the cheaper mistake.
 */

const UA = "exo-checker/1.0";

/** A source: how to read its current watermark, and what to fire when it moves. */
const SOURCES = [
  {
    name: "notion",
    event: "notes-changed",
    /**
     * `POST /v1/search` returns every connected page with its
     * `last_edited_time`, one request per hundred pages. The watermark is the
     * newest of them, so this reads ONE page of results sorted descending
     * rather than walking the workspace — the question is "did anything move",
     * not "what moved".
     */
    async watermark(env) {
      if (!env.NOTION_TOKEN) return null;
      const res = await fetch("https://api.notion.com/v1/search", {
        method: "POST",
        headers: {
          authorization: `Bearer ${env.NOTION_TOKEN}`,
          "notion-version": "2022-06-28",
          "content-type": "application/json",
          "user-agent": UA,
        },
        body: JSON.stringify({
          page_size: 1,
          sort: { direction: "descending", timestamp: "last_edited_time" },
        }),
      });
      if (!res.ok) throw new Error(`notion ${res.status}`);
      const body = await res.json();
      return body?.results?.[0]?.last_edited_time ?? null;
    },
  },
  {
    name: "lastfm",
    event: "consumption-changed",
    async watermark(env) {
      if (!env.LASTFM_API_KEY || !env.LASTFM_USER) return null;
      const qs = new URLSearchParams({
        method: "user.getRecentTracks", user: env.LASTFM_USER,
        api_key: env.LASTFM_API_KEY, format: "json", limit: "1",
      });
      const res = await fetch(`https://ws.audioscrobbler.com/2.0/?${qs}`, {
        headers: { "user-agent": UA },
      });
      if (!res.ok) throw new Error(`lastfm ${res.status}`);
      const body = await res.json();
      let tracks = body?.recenttracks?.track ?? [];
      if (!Array.isArray(tracks)) tracks = [tracks];
      // The now-playing entry has no date and would change on every check,
      // firing a lane for a song that has not been scrobbled yet.
      const played = tracks.find((t) => t?.date?.uts);
      return played?.date?.uts ?? null;
    },
  },
  {
    name: "raindrop",
    event: "consumption-changed",
    async watermark(env) {
      if (!env.RAINDROP_TOKEN) return null;
      const res = await fetch(
        "https://api.raindrop.io/rest/v1/raindrops/0?perpage=1&sort=-created",
        { headers: { authorization: `Bearer ${env.RAINDROP_TOKEN}`, "user-agent": UA } },
      );
      if (!res.ok) throw new Error(`raindrop ${res.status}`);
      const body = await res.json();
      return body?.items?.[0]?._id ? String(body.items[0]._id) : null;
    },
  },
];

/**
 * Fire one lane. `client_payload` carries what moved, so a run's logs say why
 * it exists — a lane that cannot explain its own trigger is one nobody can
 * debug six weeks later.
 */
async function dispatch(env, event, payload) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "content-type": "application/json",
        "user-agent": UA,
      },
      body: JSON.stringify({ event_type: event, client_payload: payload }),
    },
  );
  if (!res.ok) throw new Error(`dispatch ${event}: ${res.status} ${await res.text()}`);
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil((async () => {
      const fired = new Set();

      for (const source of SOURCES) {
        try {
          const now = await source.watermark(env);
          // A source with no credentials configured is skipped, not failed.
          // An instance may not hold every one of these.
          if (now === null) continue;

          const key = `cursor:${source.name}`;
          const before = await env.CURSORS.get(key);
          if (before === now) continue;

          // Write the cursor BEFORE dispatching. The other order re-fires
          // forever if the dispatch succeeds and the KV write does not — and
          // a lane that fires every fifteen minutes on unchanged data is worse
          // than one that misses a change until the next real one.
          await env.CURSORS.put(key, String(now));

          if (!fired.has(source.event)) {
            fired.add(source.event);
            await dispatch(env, source.event, { source: source.name, watermark: now });
            console.log(`${source.name}: ${before ?? "(none)"} -> ${now}, fired ${source.event}`);
          } else {
            console.log(`${source.name}: moved; ${source.event} already fired this run`);
          }
        } catch (err) {
          // One rotten source must not stop the others being checked. There is
          // nobody to read a thrown error here, and the next run tries again.
          console.log(`${source.name}: check failed (${err.message}) — will retry next run`);
        }
      }

      if (fired.size === 0) console.log("nothing moved");
    })());
  },
};

// NOTE: there is deliberately no `fetch` export. See the header. Adding one
// gives this Worker's upstream tokens a request surface, which is the whole
// thing keeping it separate from the read surface.
