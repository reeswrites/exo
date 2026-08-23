/**
 * surface.js on its own — no D1, no bundle, no corpus.
 *
 * Separate from run.mjs because run.mjs loads harness.mjs, which needs a
 * published bundle on disk. The rules this file checks are the ones that must
 * hold when there is NO bundle, which is exactly the case that harness cannot
 * construct. Run it with `node test/surface.mjs`.
 */
import { loadSurface, offers, peerFor, TTL_MS } from "../src/surface.js";

let pass = 0, fail = 0;
const ok = (c, m) => { c ? (pass++, console.log("  ok   " + m)) : (fail++, console.log("  FAIL " + m)); };

/** An R2 stub holding one object, or nothing.
 *
 * Each gets a FRESH etag by default. The loader keeps a parsed copy while the
 * version is unchanged — correct against real R2, where the etag moves whenever
 * the object does, and a trap for a test that reuses one string across stubs. */
let etagN = 0;
const r2 = (body, etag = `v${++etagN}`) => ({
  VECTORS: {
    head: async () => (body === null ? null : { etag }),
    get: async () => ({ text: async () => (typeof body === "string" ? body : JSON.stringify(body)) }),
  },
});

// Each call advances the clock past the lease so the module-level cache never
// answers for the previous stub. Same reason exposure.js bounds by wall clock.
let clock = 0;
const load = (env) => loadSurface(env, (clock += TTL_MS * 2));

console.log("\n── it fails OPEN, which is the whole asymmetry ──");
ok((await load(r2(null))).offered === null, "no surface.json -> no restriction");
ok((await load(r2("{ not json"))).offered === null, "malformed json -> no restriction");
ok((await load(r2({ tools: "notes_on" }))).offered === null, "tools that is not an array -> no restriction");
ok((await load({ VECTORS: { head: async () => { throw new Error("R2 down"); } } })).offered === null,
   "an R2 outage -> no restriction, and no throw");
ok(offers(await load(r2(null)), "anything_at_all"), "an unrestricted surface offers every tool");

console.log("\n── an explicit list is honoured, empty included ──");
const two = await load(r2({ tools: ["notes_on", "posts"] }));
ok(offers(two, "notes_on") && offers(two, "posts"), "listed tools are offered");
ok(!offers(two, "recipes"), "an unlisted tool is not offered");
const none = await load(r2({ tools: [] }));
ok(none.offered !== null && !offers(none, "notes_on"),
   "[] is an instance that deliberately offers nothing, NOT a missing file");

console.log("\n── peers ──");
const peered = await load(r2({ tools: [], peers: { notion: "Notion" } }));
ok(peerFor(peered, "notion")?.server === "Notion", "a peered source resolves to its server");
ok(peerFor(peered, "apple-notes") === null, "an unpeered source resolves to null");
ok(peerFor(peered, undefined) === null, "a row with no source resolves to null");
ok(peerFor(await load(r2({ tools: [] })), "notion") === null, "no peers declared -> null");
ok(peerFor(null, "notion") === null, "no surface at all -> null, not a throw");

console.log("\n── the lease ──");
const env1 = r2({ tools: ["notes_on"] }, "etag-1");
const t = (clock += TTL_MS * 2);
await loadSurface(env1, t);
let hits = 0;
env1.VECTORS.head = async () => { hits++; return { etag: "etag-1" }; };
await loadSurface(env1, t + 1);
ok(hits === 0, "a warm cache inside the lease does not re-ask R2");
await loadSurface(env1, t + TTL_MS + 1);
ok(hits === 1, "past the lease it re-checks, so a tightened list cannot be served indefinitely");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
