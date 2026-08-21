/**
 * How public each served zone is (ADR-0019), and what that makes a tool.
 *
 * `exposure.json` is written by `exo publish`, which resolves absence there and
 * lists every served zone explicitly. Nothing out here re-decides a grade: a
 * default chosen in two places is a default that will eventually differ.
 *
 * ## Fail-closed, and it has to survive its own failures
 *
 * Every path that cannot produce an answer produces `private`. A missing object,
 * a malformed one, an R2 outage, a grade spelled in a way this build does not
 * recognise — all of them land on the tightest reading. Nothing here throws,
 * either: a surface that returned 500 because a metadata file was absent would
 * convert a publicity question into an outage, and the honest response to "I do
 * not know how public this is" is to treat it as private and keep answering.
 *
 * ## Staleness is the real hazard
 *
 * An isolate outlives a publish. If a grade is tightened — an account made
 * private, a zone reclassified — a warm isolate holding the old file would serve
 * newly-private material under public caps, which is the one direction of error
 * this whole axis exists to prevent. So the cache is bounded by wall clock, not
 * only by etag: after TTL_MS we ask R2 whether the object changed, and only
 * re-download when it did. Cheap, and bounded rather than indefinite.
 */

/** Ordered least public first, because "least public wins" is the rule at every level. */
export const GRADES = ["private", "profile", "published"];
export const DEFAULT_GRADE = "private";

// How long a cached copy is trusted without asking R2 whether it changed. One
// minute bounds how long a tightened grade can keep being read as the old one.
export const TTL_MS = 60_000;

let CACHE = null; // { zones, version, checked } — per-isolate, like the vector index

/**
 * The zone -> grade map, or an empty one. Never throws.
 *
 * An empty map is not an error state that needs handling downstream: every
 * lookup defaults to `private`, so "no exposure data" and "everything is
 * private" are the same fact and produce the same behaviour.
 */
export async function loadExposure(env, now = Date.now()) {
  if (CACHE && now - CACHE.checked < TTL_MS) return CACHE.zones;

  try {
    const head = await env.VECTORS.head("exposure.json");
    const version = head?.etag ?? head?.uploaded?.toISOString?.() ?? null;

    // Unchanged since last time: keep the parsed map, just extend its lease.
    if (CACHE && CACHE.version === version) {
      CACHE.checked = now;
      return CACHE.zones;
    }
    if (!head) {
      CACHE = { zones: {}, version: null, checked: now };
      return CACHE.zones;
    }

    const obj = await env.VECTORS.get("exposure.json");
    const parsed = JSON.parse(await obj.text());
    const zones = {};
    for (const [zone, grade] of Object.entries(parsed?.zones ?? {})) {
      // A grade this build does not know is not a grade. Publishing may have
      // learned a fourth one; until this worker is deployed with it, the safe
      // reading of a word it cannot interpret is the tightest one.
      zones[zone] = GRADES.includes(grade) ? grade : DEFAULT_GRADE;
    }
    CACHE = { zones, version, checked: now };
    return zones;
  } catch {
    // Deliberately swallowed. Do NOT cache a failure as if it were data — the
    // next call should try again rather than inherit an outage for a minute.
    return CACHE?.zones ?? {};
  }
}

/**
 * A tool is as public as the least public zone it reads (ADR-0019 §2).
 *
 * `reads` names the zones whose CONTENT can reach the caller, which is not the
 * same as the tables a query touches. A vector index is machinery: `search.js`
 * returns labels and scores, never a vector, so `t2_post_vec` is not something
 * the `posts` tool can leak and does not drag it down to private. A table whose
 * rows can appear in an answer belongs here; one that only ranks them does not.
 *
 * A tool that reads nothing is `private`. That is not a hedge — it is a tool
 * whose declaration nobody wrote, and an undeclared thing is exactly what the
 * fail-closed rule is for.
 */
export function gradeOf(zones, reads) {
  if (!Array.isArray(reads) || reads.length === 0) return DEFAULT_GRADE;
  let least = GRADES.length - 1;
  for (const zone of reads) {
    const i = GRADES.indexOf(zones?.[zone] ?? DEFAULT_GRADE);
    least = Math.min(least, i < 0 ? 0 : i);
    if (least === 0) return DEFAULT_GRADE; // cannot get tighter; stop looking
  }
  return GRADES[least];
}
