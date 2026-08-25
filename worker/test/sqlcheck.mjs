/**
 * Prepare every SQL statement the tools can emit, against the real published
 * schema. No data required: SQLite validates table and column references at
 * prepare time, which is exactly the class of mistake a stub returning `{rows:[]}`
 * cannot catch — an ORDER BY naming a column that is not in scope after a GROUP
 * BY parses fine as a string and fails only when a caller asks the question.
 *
 *   EXO_HOME=<instance> node test/sqlcheck.mjs
 */
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import path from "node:path";
import { TOOLS } from "../src/tools.js";

const CF = path.resolve(process.env.EXO_HOME, "zones/_serve/cf");
const db = new DatabaseSync(":memory:");
db.exec(readFileSync(path.join(CF, "schema.sql"), "utf8"));

// Shims for zones this bundle happens not to carry — a plugin zone, or one that
// was empty when it was published. Without them those queries are skipped
// entirely, which is the wrong kind of quiet: `events` is the most intricate SQL
// on the surface and it is exactly the one an incomplete fixture hides.
// Columns come from the queries themselves; only names and arity matter here,
// because prepare() checks references and never touches a row.
for (const [table, cols] of Object.entries({
  t0_event: ["id", "title", "feed", "venue", "location", "free", "url", "start", "description", "created"],
  t0_chat_topic: ["id", "title", "landed", "turns", "his_turns", "last_seen", "started", "summary", "summary_by", "created"],
  t1_taste: ["id", "kind", "key", "value", "created"],
  t0_taste_derived: ["id", "kind", "text", "created"],
  t0_release: ["id", "artist", "title", "release_date", "url", "label", "art", "mbid",
               "mb_status", "scenes", "scene_count", "listings", "medium", "created"],
  t0_criticism: ["id", "outlet", "outlet_slug", "title", "byline", "published", "url",
                 "summary", "chars", "tags", "medium", "created"],
  // An instance that has never dropped a MyAnimeList export into raw/ still has
  // to have `watching` and `ratings(medium:'anime')` checked: the join in
  // `watching` is the most intricate SQL added since `events`, and an absent
  // zone would skip exactly it.
  t0_anime: ["id", "title", "mal_id", "score", "status", "episodes_watched",
             "episodes_total", "series_type", "rewatches", "started", "finished",
             "last_updated", "url", "match_key", "created"],
  // The other side of that join, for a bundle carrying no television at all —
  // without it the whole statement is filed as "absent zone" and goes
  // unchecked. It does NOT paper over a stale bundle: a t0_tv published before
  // `match_key` existed is a real table, so the shim does not apply and the
  // missing column is reported, which is the right answer (republish).
  t0_tv: ["id", "title", "year", "plays", "episodes_watched", "seasons_touched",
          "imdb", "url", "match_key", "created"],
})) {
  const exists = db.prepare("SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name=?").get(table);
  if (!exists.n) db.exec(`CREATE TABLE "${table}" (${cols.map((c) => `"${c}"`).join(", ")})`);
}

const seen = new Set();
const bad = [];
const absent = [];
const env = {
  DB: {
    prepare(sql) {
      if (!seen.has(sql)) {
        seen.add(sql);
        // "no such table" means this instance's bundle lacks a zone — a plugin
        // zone, or one that happened to be empty. "no such column" means the
        // code named something that is not there, which is the bug being hunted.
        try { db.prepare(sql); }
        catch (e) { (/no such table/.test(e.message) ? absent : bad).push({ sql, msg: e.message }); }
      }
      return { bind() { return this; }, async all() { return { results: [] }; }, async first() { return null; } };
    },
  },
  VECTORS: { head: async () => null, get: async () => null },
  AI: { run: async () => ({ data: [Array.from({ length: 384 }, () => 0.1)] }) },
  BLOG_URL_TEMPLATE: "https://example.com/posts/{slug}/",
};

// Argument shapes that steer tools down their different query branches.
const ARGS = [
  {}, { topic: "x" }, { full: true }, { kind: "read" }, { kind: "film" },
  { medium: "film" }, { order: "oldest" }, { repo: "x" }, { city: "x" },
  { group: "x" }, { since: "2020-01-01" }, { min_turns: 2 }, { free: true },
  // taste's branches: the artist filter, the closed window, the optional join
  // onto the affinity zone, and each of its three axes.
  { artist: "x" }, { since: "2020-01-01", until: "2021-01-01" },
  { with_mentions: true }, { order: "recent" }, { order: "played" },
];
for (const [name, t] of Object.entries(TOOLS)) {
  for (const args of ARGS) {
    for (const ctx of [undefined, { exposure: "published" }]) {
      try { await t.run(env, { ...args }, ctx); } catch { /* data-shaped failures are fine */ }
    }
  }
}

console.log(`prepared ${seen.size} distinct statements`);
if (absent.length) {
  const zones = [...new Set(absent.map((a) => a.msg.replace("no such table: ", "")))];
  console.log(`  ${absent.length} skipped — zones absent from this bundle: ${zones.join(", ")}`);
}
if (bad.length) {
  for (const b of bad) console.log(`\nFAIL ${b.msg}\n  ${b.sql.replace(/\s+/g, " ").slice(0, 200)}`);
  process.exit(1);
}
console.log("every statement prepares against the published schema");
