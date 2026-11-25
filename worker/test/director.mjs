/**
 * ratings(director:) against an in-memory t0_film — no bundle needed.
 * `node worker/test/director.mjs`
 */
import { DatabaseSync } from "node:sqlite";
import { TOOLS } from "../src/tools.js";

const db = new DatabaseSync(":memory:");
db.exec(`CREATE TABLE t0_film (id TEXT, title TEXT, year TEXT, rating TEXT, avg_rating TEXT,
  director TEXT, origin_ref TEXT, created TEXT)`);
const ins = db.prepare("INSERT INTO t0_film VALUES (?,?,?,?,?,?,?,?)");
ins.run("a", "Women in Love", "1969", "4", "3.6", "Ken Russell", "https://boxd.it/a", "2024-01-05");
ins.run("b", "The Devils", "1971", "", "4.0", "Ken Russell", "https://boxd.it/b", "2024-03-01");
ins.run("c", "Fargo", "1996", "4.5", "4.1", "Joel Coen, Ethan Coen", "https://boxd.it/c", "2023-05-01");
ins.run("d", "Stalker", "1979", "5", "4.3", "", "https://boxd.it/d", "2022-01-01");

const env = { DB: { prepare(sql) { return {
  bind(...b) { this._b = b; return this; },
  async all() { return { results: db.prepare(sql).all(...(this._b ?? [])) }; },
}; } } };
const ctx = { grade: "private" };
let fails = 0;
const ok = (c, m) => { console.log(`${c ? "  ok  " : "  FAIL"} ${m}`); if (!c) fails++; };
const run = (a) => TOOLS.ratings.run(env, a, ctx);

const kr = await run({ director: "ken russell" });
ok(kr.rows.map((r) => r.label).join("|") === "The Devils|Women in Love", "every film by the director, newest first");
ok(kr.rows[0].rating === null, "a film logged without stars is still listed, rating null");
ok(kr.rows.every((r) => r.director === "Ken Russell" && r.medium === "films"), "rows carry the director");
ok((await run({ director: "Ethan" })).rows[0]?.label === "Fargo", "co-directors match on either name");
ok((await run({ director: "Russell", medium: "books" })).rows.length === 2, "director narrows to films whatever the medium");
const miss = await run({ director: "Varda" });
ok(miss.rows.length === 0 && /few nights/.test(miss.note), "a miss says directors fill in over time");
const ranked = await run({ medium: "films" });
ok(ranked.rows[0].label === "Stalker" && !("director" in ranked.rows[0]), "an empty director is absent, not blank");
ok(ranked.rows.find((r) => r.label === "Fargo").director === "Joel Coen, Ethan Coen", "ranked rows carry the director too");

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log("\nall passed");
