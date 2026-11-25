/**
 * saves by meaning, against an in-memory table and a fake R2 index — no bundle
 * needed. `node worker/test/saves.mjs`
 */
import { DatabaseSync } from "node:sqlite";
import { TOOLS } from "../src/tools.js";

const db = new DatabaseSync(":memory:");
db.exec(`CREATE TABLE t0_raindrop (id TEXT, title TEXT, url TEXT, kind TEXT, platform TEXT,
  collection TEXT, tags TEXT, note TEXT, excerpt TEXT, created TEXT)`);
const ins = db.prepare("INSERT INTO t0_raindrop VALUES (?,?,?,?,?,?,?,?,?,?)");
ins.run("s1", "You won't believe this one trick", "https://a", "article", "substack",
        "thought-provoking", "", "on loneliness in cities", "", "2025-01-01");
ins.run("s2", "Glaze recipes", "https://b", "link", "web", "yummy", "", "", "pottery glazes", "2025-02-01");
ins.run("s3", "Urban isolation essay", "https://c", "video", "youtube", "thought-provoking", "", "", "", "2025-03-01");
ins.run("s4", "Lonely city walks", "https://d", "article", "substack", "want-to-think-about", "", "", "", "2025-04-01");

// Two dimensions: axis 0 = loneliness, axis 1 = pottery.
const VEC = { s1: [1, 0], s2: [0, 1], s3: [0.9, 0.436], s4: [0.8, 0.6] };
const order = ["s1", "s2", "s3", "s4"];
const blob = new Float32Array(order.flatMap((id) => VEC[id])).buffer;
const meta = { dim: 2, count: 4, normalized: true,
               rows: order.map((id) => ({ kind: "save", id, label: id })) };

function env({ published = true } = {}) {
  return {
    DB: { prepare(sql) { return {
      bind(...b) { this._b = b; return this; },
      async all() { return { results: db.prepare(sql).all(...(this._b ?? [])) }; },
    }; } },
    VECTORS: {
      async head(k) { return published && k.startsWith("saves.") ? { etag: "v1" } : null; },
      async get(k) {
        if (!published) return null;
        if (k === "saves.f32") return { arrayBuffer: async () => blob };
        if (k === "saves.json") return { text: async () => JSON.stringify(meta) };
        return null;
      },
    },
    AI: { async run(_m, { text: [t] }) { return { data: [/glaze|pottery/.test(t) ? [0, 1] : [1, 0]] }; } },
  };
}
const ctx = { grade: "private" };
let fails = 0;
const ok = (c, m) => { console.log(`${c ? "  ok  " : "  FAIL"} ${m}`); if (!c) fails++; };

const r = await TOOLS.saves.run(env(), { topic: "feeling alone in a big city" }, ctx);
ok(r.rows[0].title === "You won't believe this one trick", "finds a save by its note, not its title");
ok(r.rows.map((x) => x.title).join("|").startsWith("You won't believe this one trick|Urban isolation essay|Lonely city walks"),
   "best match first");
ok(typeof r.rows[0].score === "number" && !("id" in r.rows[0]), "carries a score, never the internal id");
ok(/meaning/.test(r.matched_by), "says it matched by meaning");

const f = await TOOLS.saves.run(env(), { topic: "loneliness", platform: "youtube" }, ctx);
ok(f.rows.length === 1 && f.rows[0].title === "Urban isolation essay", "filters still apply to meaning hits");

const w = await TOOLS.saves.run(env({ published: false }), { topic: "glazes" }, ctx);
ok(w.rows.length === 1 && w.rows[0].title === "Glaze recipes" && /words/.test(w.matched_by),
   "with no saves index, falls back to words — and words now reach the excerpt");

const axes = await TOOLS.saves.run(env(), {}, ctx);
ok(axes.rows.length === 0 && axes.platforms.length > 0, "no arguments still returns the axes");

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log("\nall passed");
