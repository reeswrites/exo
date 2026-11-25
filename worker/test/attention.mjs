/**
 * recent_topics over t0_attention_thread, against an in-memory table — no
 * bundle needed, so it runs anywhere. `node worker/test/attention.mjs`
 */
import { DatabaseSync } from "node:sqlite";
import { TOOLS } from "../src/tools.js";

const db = new DatabaseSync(":memory:");
db.exec(`CREATE TABLE t0_attention_thread (id TEXT, title TEXT, summary TEXT, moves TEXT,
  touches INTEGER, load TEXT, first_seen TEXT, last_touched TEXT, authorship TEXT)`);
const ins = db.prepare("INSERT INTO t0_attention_thread VALUES (?,?,?,?,?,?,?,?,?)");
ins.run("a", "Housing plan", "Weighing rent against buying.", "2026-11-20 started\n2026-11-24 ran the numbers",
        4, "Heavy", "2026-11-20", "2026-11-24T18:00:00.000Z", "AI");
ins.run("b", "Horror and the subconscious", "Why dread works.", "2026-10-01 a film",
        12, "Medium", "2026-09-01", "2026-11-10", "Mixed");
ins.run("c", "Pottery", "Glaze chemistry.", "", 1, "Light", "2026-11-01", "2026-11-01", "AI");

const env = { DB: { prepare(sql) { return {
  bind(...b) { this._b = b; return this; },
  async all() { return { results: db.prepare(sql).all(...(this._b ?? [])) }; },
}; } } };
const ctx = { grade: "private" };

let fails = 0;
const ok = (c, m) => { console.log(`${c ? "  ok  " : "  FAIL"} ${m}`); if (!c) fails++; };
const run = (args) => TOOLS.recent_topics.run(env, args, ctx);

const all = await run({});
ok(all.rows.map((r) => r.title).join("|") === "Housing plan|Horror and the subconscious|Pottery",
   "newest-touched first by default");
ok(all.rows[0].last_touched === "2026-11-24", "dates come back as days");
ok(all.rows[0].latest_move === "2026-11-24 ran the numbers" && all.rows[0].move_count === 2,
   "the latest move, not the whole list, unless full");
ok(typeof all.rows[0].days_since_touched === "number", "recency is computed at ask time");
ok(/machine-written/.test(all.voice), "says the summaries are not the owner's words");
ok(/3 threads/.test(all.scope), "scope counts the threads");

ok((await run({ order: "touches" })).rows[0].title === "Horror and the subconscious", "order=touches");
ok((await run({ min_touches: 4 })).rows.length === 2, "min_touches floor");
ok((await run({ since: "2026-11-05" })).rows.length === 2, "since filters on last touched");
ok((await run({ topic: "dread" })).rows[0]?.title === "Horror and the subconscious", "topic matches the summary");
ok((await run({ topic: "numbers" })).rows[0]?.title === "Housing plan", "topic matches the moves");
ok((await run({ full: true })).rows[0].moves.length === 2, "full returns every move");
const miss = await run({ topic: "sourdough" });
ok(miss.rows.length === 0 && /notes_on/.test(miss.note), "a miss points at notes_on");

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log("\nall passed");
