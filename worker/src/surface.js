/**
 * Which tools this instance offers (ADR-0020), and the peers it knows about.
 *
 * `surface.json` is written by `exo publish`, which resolves the whole question
 * there: a tool is dropped because a zone it needs is held, because the owner
 * disabled it, or because it falls outside the domains this instance keeps.
 * Nothing out here re-decides any of that — the same rule `exposure.js` follows,
 * for the same reason. The worker cannot see `exo.toml` at all.
 *
 * ## This one fails OPEN, and that is the point
 *
 * `exposure.js` resolves every failure to `private`, because a grade it cannot
 * read is a grade it must assume is tight. This module does the opposite and
 * the asymmetry is deliberate:
 *
 *   - It cannot widen what leaves. A held zone is ABSENT from the projection,
 *     so a tool reaching for it finds nothing whether or not it was listed. The
 *     tool list says what is worth calling; the D1 import says what exists.
 *
 *   - Failing closed would hide every newly-shipped tool until each instance
 *     named it, which is the "publishing is not offering" failure ADR-0013
 *     called a standing duty after `t1_item` sat in production for weeks with
 *     nothing reading it. A worker deployed ahead of its bundle should serve
 *     the tools it has, not none of them.
 *
 * So: no file, a malformed file, an R2 outage — all mean "offer everything".
 * `null` is that answer, and it is distinct from `[]`, which is an instance
 * that deliberately offers nothing.
 */

// Same lease as the exposure map, and for the same reason: an isolate outlives
// a publish, so a bounded wall-clock check is what stops a warm one advertising
// a tool the owner turned off an hour ago.
export const TTL_MS = 60_000;

let CACHE = null; // { offered: Set|null, peers, version, checked }

const EMPTY = { offered: null, peers: {} };

/**
 * `{ offered, peers }`. `offered === null` means no restriction. Never throws.
 */
export async function loadSurface(env, now = Date.now()) {
  if (CACHE && now - CACHE.checked < TTL_MS) return CACHE;

  try {
    const head = await env.VECTORS.head("surface.json");
    const version = head?.etag ?? head?.uploaded?.toISOString?.() ?? null;

    if (CACHE && CACHE.version === version) {
      CACHE.checked = now;
      return CACHE;
    }
    if (!head) {
      CACHE = { ...EMPTY, version: null, checked: now };
      return CACHE;
    }

    const parsed = JSON.parse(await (await env.VECTORS.get("surface.json")).text());
    // An absent or non-array `tools` is not an empty surface. It is a file this
    // build cannot interpret, and the open reading is the safe one here.
    const offered = Array.isArray(parsed?.tools) ? new Set(parsed.tools) : null;
    const peers = parsed?.peers && typeof parsed.peers === "object" ? parsed.peers : {};
    CACHE = { offered, peers, version, checked: now };
    return CACHE;
  } catch {
    // Do NOT cache a failure as data — the next call should try again rather
    // than inherit an outage for a minute.
    return CACHE ?? EMPTY;
  }
}

/** Is this tool offered here? Unrestricted surfaces offer everything. */
export function offers(surface, name) {
  return !surface?.offered || surface.offered.has(name);
}

/**
 * The peer that also serves rows of this provenance, or null.
 *
 * Exo states the fact and stops there (ADR-0013 §2). Whether to go read the
 * live copy, cite it, or ignore the duplication entirely is the agent's call —
 * it knows what it already has in context and we do not.
 */
export function peerFor(surface, source) {
  if (!source) return null;
  const server = surface?.peers?.[source];
  return server ? { server, source } : null;
}
