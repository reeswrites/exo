import { readFileSync } from "node:fs";
import { env, corpus } from "./harness.mjs";
import { TOOLS, CLASSES, DOMAINS, KINDS, cap, probe, pageSize, ROW_CAP, MAX_ROWS, MAX_BYTES } from "../src/tools.js";
import { GRADES, DEFAULT_GRADE, gradeOf, loadExposure, TTL_MS } from "../src/exposure.js";
import worker from "../src/index.js";

let pass = 0, fail = 0;
const ok = (c, m) => { c ? (pass++, console.log("  ok   " + m)) : (fail++, console.log("  FAIL " + m)); };
const post = (body, token = "test-token") =>
  worker.fetch(new Request("https://x/", {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  }), env);

console.log("\n── auth ──");
ok((await post({ jsonrpc: "2.0", id: 1, method: "ping" }, "wrong")).status === 401, "wrong token rejected");
ok((await post({ jsonrpc: "2.0", id: 1, method: "ping" })).status === 200, "correct token accepted");
const batch = await worker.fetch(new Request("https://x/", { method: "POST",
  headers: { authorization: "Bearer test-token" }, body: "[]" }), env);
ok((await batch.json()).error?.code === -32600, "batch requests refused (would multiply the cap)");

console.log("\n── protocol ──");
const init = await (await post({ jsonrpc: "2.0", id: 1, method: "initialize" })).json();
ok(init.result?.serverInfo?.name === "exo", "initialize returns serverInfo");
const list = await (await post({ jsonrpc: "2.0", id: 2, method: "tools/list" })).json();
ok(list.result.tools.length === Object.keys(TOOLS).length, `tools/list -> ${list.result.tools.length} tools`);
ok(list.result.tools.every((t) => t.description && t.inputSchema), "every tool has description + schema");
const res = await (await post({ jsonrpc: "2.0", id: 3, method: "resources/read", params: { uri: "warehouse://brief" } })).json();
ok(/^# \S/.test(res.result.contents[0].text), "brief resource served from R2");

console.log("\n── procedures as resources (ADR-0016) ──");
// Data-agnostic on purpose: an instance may hold no procedures at all, and the
// empty case is the one a fresh instance ships with. What is asserted is the
// SHAPE — the brief still leads, every entry addresses one slug, and nothing
// but a served slug resolves.
const rlist = await (await post({ jsonrpc: "2.0", id: 10, method: "resources/list" })).json();
const rs = rlist.result?.resources ?? [];
ok(rs[0]?.uri === "exo://brief", "the brief is still pinned first");
const procs = rs.filter((r) => r.uri.startsWith("exo://procedure/"));
ok(procs.every((r) => /^exo:\/\/procedure\/[a-z0-9-]+$/.test(r.uri)),
   `every procedure uri is one addressable slug (${procs.length} listed)`);
ok(procs.every((r) => r.name && r.description && r.mimeType === "text/markdown"),
   "each carries a name, a description and a mime type");
ok(procs.every((r) => /Reports only\.|Acts on their behalf\./.test(r.description)),
   "the description says whether following it acts");

const readUri = (uri, id = 11) =>
  post({ jsonrpc: "2.0", id, method: "resources/read", params: { uri } });

if (procs.length) {
  const one = await (await readUri(procs[0].uri)).json();
  const text = one.result?.contents?.[0]?.text ?? "";
  ok(text.includes("own method, written by hand"),
     "a procedure reads as the owner's document, not as instructions from the server");
  ok(/\*\*When it applies:\*\*/.test(text), "it states its own trigger");
  const abortAt = text.indexOf("Stop before you start");
  const stepsAt = text.indexOf("## The procedure");
  ok(stepsAt > 0 && (abortAt === -1 || abortAt < stepsAt),
     "blocking preconditions come above the steps, never after them");
  ok(/Last verified|never marked this verified/.test(text), "it states its own age");
  if (/\*\*Kind:\*\* action/.test(text))
    ok(text.includes("may never choose or change a target"),
       "an acting procedure fixes its own sink and target");
  else ok(true, "the first procedure reports rather than acts (nothing to fix)");

  const auditedRead = (await env.DB.prepare(
    "SELECT count(*) AS n FROM wh_audit WHERE tool = 'resources/read'").bind().all()).results[0];
  ok(auditedRead.n > 0, "a procedure read lands in wh_audit like any other call");
}

for (const bad of ["exo://procedure/nothing-by-this-name", "exo://procedure/../brief",
                   "exo://procedure/Weekly-Digest", "exo://procedure/", "exo://procedure/a/b"]) {
  const r = await (await readUri(bad, 12)).json();
  ok(r.error?.code === -32602, `refused: ${bad}`);
}
// What is NOT checkable from here: that a held procedure is absent. This side
// cannot know what was held — which is the property itself. A held procedure is
// missing from the bundle entirely, so it answers exactly as one that never
// existed does, and the proof lives where the decision does
// (tests/test_procedures.py, against the manifest).
const briefStill = await (await readUri("exo://brief", 13)).json();
ok(/^# \S/.test(briefStill.result?.contents?.[0]?.text ?? ""),
   "the brief still resolves beside them");

console.log("\n── the row cap follows the axis, the byte cap does not ──");
ok(ROW_CAP.private === MAX_ROWS, "private is the floor, and the floor is ADR-0007's twenty");
ok(ROW_CAP.profile > ROW_CAP.private && ROW_CAP.published >= ROW_CAP.profile,
   `ceilings rise with publicity (${ROW_CAP.private}/${ROW_CAP.profile}/${ROW_CAP.published})`);
ok(pageSize({ exposure: "published" }) === ROW_CAP.published, "a published tool may return more");
ok(pageSize({}) === ROW_CAP.private, "an ungraded call gets the private ceiling");
ok(pageSize(undefined) === ROW_CAP.private, "and so does one with no context at all");
ok(pageSize({ exposure: "nonsense" }) === ROW_CAP.private, "an unknown grade gets the private ceiling");

// `limit` narrows and can never widen. This is the whole difference between an
// ergonomics parameter and a hole in ADR-0007.
ok(pageSize({ exposure: "published", limit: 5 }) === 5, "limit narrows");
ok(pageSize({ exposure: "private", limit: 500 }) === MAX_ROWS, "limit cannot widen past the grade");
ok(pageSize({ exposure: "published", limit: 0 }) >= 1, "a zero limit is not a zero-row answer");
ok(pageSize({ exposure: "published", limit: -3 }) === ROW_CAP.published, "a negative limit is ignored, not obeyed");
ok(pageSize({ exposure: "published" }, 5) === 5, "a tool's own ceiling narrows below the grade");

// The byte cap does NOT follow the axis: it protects the caller's context
// window, which does not care who may read the rows.
const wide = Array.from({ length: ROW_CAP.published }, () => ({ t: "y".repeat(400) }));
ok(JSON.stringify(cap(wide, { exposure: "published" }).rows).length <= MAX_BYTES,
   "a published answer still fits the 16KB budget");

// A raised ceiling must not cost the honesty won in the previous phase.
const over = Array.from({ length: probe({ exposure: "profile" }) }, (_, i) => ({ i }));
ok(cap(over, { exposure: "profile" }).returned_count === ROW_CAP.profile, "profile returns its ceiling");
ok(cap(over, { exposure: "profile" }).has_more === true, "and still says there is more");
ok(cap(over).returned_count === MAX_ROWS, "the same rows with no context stay at twenty");

// Every tool survives being called with and without a context. A tool that
// destructured `ctx` in a branch nobody exercised would throw only in
// production, on whichever grade nobody tested.
let scoped = 0;
for (const [, t] of Object.entries(TOOLS)) {
  for (const c of [undefined, { exposure: "published", limit: 3 }]) {
    try { await t.run(env, { topic: "x" }, c); scoped++; }
    catch (e) { if (!/is not defined/.test(e.message)) scoped++; }
  }
}
ok(scoped === Object.keys(TOOLS).length * 2, "every tool runs with and without a context");

console.log("\n── the publicity axis (ADR-0019) ──");
// Every path that cannot produce an answer must produce the tightest one. These
// are not edge cases: a missing file is what a worker deployed ahead of its
// bundle sees, and it has to serve tight rather than open.
const zones = { t1_post: "published", t0_music: "profile", t1_notes: "private" };
ok(gradeOf(zones, ["t1_post"]) === "published", "a published zone grades published");
ok(gradeOf(zones, ["t1_post", "t1_notes"]) === "private", "least public zone wins over the tool");
ok(gradeOf(zones, ["t1_post", "t0_music"]) === "profile", "published + profile -> profile");
ok(gradeOf(zones, ["t0_nonexistent"]) === DEFAULT_GRADE, "a zone the bundle never graded is private");
ok(gradeOf(zones, []) === DEFAULT_GRADE, "a tool that declares nothing is private");
ok(gradeOf(zones, undefined) === DEFAULT_GRADE, "an absent declaration is private");
ok(gradeOf({ x: "semi-public" }, ["x"]) === DEFAULT_GRADE,
   "a grade this build cannot interpret is private, not passed through");
ok(GRADES[0] === DEFAULT_GRADE, "the vocabulary is ordered least public first");

// Absent R2 object, and an R2 that throws. Neither may take the surface down:
// turning a publicity question into a 500 is a worse failure than answering it
// conservatively.
const noR2 = { VECTORS: { head: async () => null, get: async () => null } };
ok(Object.keys(await loadExposure(noR2, Date.now() + TTL_MS * 99)).length === 0,
   "a missing exposure.json yields no grades, which reads as all-private");
const angryR2 = { VECTORS: { head: async () => { throw new Error("R2 down"); } } };
ok(typeof (await loadExposure(angryR2, Date.now() + TTL_MS * 198)) === "object",
   "an R2 outage returns a map rather than throwing");

// Every tool declares what it reads, and the declaration is checked against the
// SQL it actually runs. A tool edited to query a new table without updating
// `reads` would be graded on the zones it USED to touch — the one direction of
// drift that can only ever be too permissive.
const undeclared = Object.entries(TOOLS).filter(([, t]) => !Array.isArray(t.reads) || !t.reads.length);
ok(undeclared.length === 0, `every tool declares reads (${undeclared.map(([n]) => n).join(", ") || "none"})`);

const ZONE = /^t[012]_[a-z_]+$/;
const badZone = Object.entries(TOOLS).flatMap(([n, t]) => (t.reads ?? []).filter((z) => !ZONE.test(z)).map((z) => `${n}:${z}`));
ok(badZone.length === 0, `reads name zones, not tables or CTEs (${badZone.join(", ") || "none"})`);

// A vector zone in `reads` is a category error, and a costly one: it would drag
// `posts` to private forever. search.js returns labels and scores, never a
// vector, so a vector index ranks an answer and can never be one.
const vecReads = Object.entries(TOOLS).flatMap(([n, t]) => (t.reads ?? []).filter((z) => z.endsWith("_vec")).map((z) => `${n}:${z}`));
ok(vecReads.length === 0, `no tool claims to read a vector zone (${vecReads.join(", ") || "none"})`);

let unverifiable = 0;
const drift = [];
for (const [name, t] of Object.entries(TOOLS)) {
  const src = t.run.toString();
  if (/\b(?:FROM|JOIN)\s+\$\{/.test(src)) unverifiable++;   // table chosen at runtime
  const declared = new Set(t.reads ?? []);
  for (const m of src.matchAll(/\b(?:FROM|JOIN)\s+(t[012]_[a-z_]+)/g)) {
    if (!declared.has(m[1])) drift.push(`${name} queries ${m[1]} and does not declare it`);
  }
}
ok(drift.length === 0, `reads match the SQL (${drift.join("; ") || "no drift"})`);
// Said out loud rather than passed silently: a check that quietly covers 24 of
// 28 tools reads exactly like one that covers all of them.
console.log(`  note ${unverifiable} tool(s) choose a table at runtime — their reads are declared, not verified`);

console.log("\n── caps (ADR-0007) ──");
const many = Array.from({ length: 500 }, (_, i) => ({ i, pad: "x".repeat(50) }));
const c = cap(many);
ok(c.rows.length <= MAX_ROWS, `row cap holds (${c.rows.length} <= ${MAX_ROWS})`);
ok(JSON.stringify(c.rows).length <= MAX_BYTES, `byte cap holds (${JSON.stringify(c.rows).length} <= ${MAX_BYTES})`);
ok(!!c.note, "truncation is announced, not silent");

console.log("\n── has_more (a cap that cannot say \"there is more\" is a cap that lies) ──");
// The defect this replaces: every SQL tool bound its LIMIT to MAX_ROWS and then
// handed the result to cap(), so cap() never saw a row it had to drop. It
// reported "20 of 20" for a shelf of 436, and an assistant reading that
// concluded the shelf was twenty books long.
const exact = cap(Array.from({ length: MAX_ROWS }, (_, i) => ({ i })));
ok(exact.returned_count === MAX_ROWS && exact.has_more === false,
   `exactly ${MAX_ROWS} rows -> has_more=false`);
ok(exact.note === undefined, "and nothing is announced, because nothing was dropped");

const probed = cap(Array.from({ length: probe() }, (_, i) => ({ i })));
ok(probed.returned_count === MAX_ROWS && probed.has_more === true,
   `one row past the cap -> has_more=true, ${probed.returned_count} returned`);
ok(!probed.rows.some((r) => r.i === MAX_ROWS), "the probe row is counted, never emitted");

// A tool with its own smaller page must detect ITS overflow, not the global one.
// `ratings` shows five per medium; without this it would report has_more=false
// on a medium with fifty rated films.
const small = cap(Array.from({ length: probe(5) }, (_, i) => ({ i })), { want: 5 });
ok(small.returned_count === 5 && small.has_more === true, "want=5 overflows at 5, not at 20");
ok(cap([{ i: 1 }], { want: 5 }).has_more === false, "want=5 under-full -> has_more=false");
ok(cap(Array.from({ length: 100 }, (_, i) => ({ i })), { want: 999 }).returned_count === MAX_ROWS,
   "want narrows the page and can never widen it past MAX_ROWS");

// The regression that actually bites: a tool binding MAX_ROWS instead of probe()
// loses the ability to say there is more, and NOTHING ELSE FAILS — the rows come
// back, the caps hold, the tests pass, and the answer quietly claims to be whole.
// So the guard is over the source, not over behaviour.
const CAP_DEFS = [
  /^\s*(\/\/|\*)/,                    // prose about the cap
  /export const MAX_ROWS/,
  /export const probe = \(want = MAX_ROWS\)/,
  /\{ want = MAX_ROWS \}/,
  /Math\.min\(want, MAX_ROWS\)/,
];
const bareBinds = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8")
  .split("\n")
  .map((line, n) => [n + 1, line])
  .filter(([, line]) => /\bMAX_ROWS\b/.test(line) && !CAP_DEFS.some((re) => re.test(line)));
ok(bareBinds.length === 0,
   `every row limit probes — bare MAX_ROWS binds: ${bareBinds.map(([n]) => n).join(", ") || "none"}`);

console.log("\n── caps, continued ──");
// sized from MAX_BYTES so raising the cap cannot silently retire this check
const fatRow = Math.ceil(MAX_BYTES / 3);
const fat = cap(Array.from({ length: 5 }, () => ({ t: "y".repeat(fatRow) })));
ok(fat.rows.length < 5 && !!fat.note,
   `byte cap binds before row cap on large rows (${fat.rows.length} of 5 at ${fatRow}B each)`);

console.log("\n── tools against real data ──");
for (const name of ["open_threads", "verdicts", "taste", "consumption", "places"]) {
  const r = await TOOLS[name].run(env, {});
  ok(Array.isArray(r.rows) && r.rows.length > 0, `${name} -> ${r.rows.length} rows`);
  ok(JSON.stringify(r).length <= MAX_BYTES + 200, `${name} within cap`);
}
const v = await TOOLS.verdicts.run(env, { kind: "films" });
ok(v.rows.every((r) => r.kind === "films"), "verdicts filters by kind");

console.log("\n── vector search against the real 16.85MB blob ──");
corpus.setProbe(0);
const hits = await TOOLS.whats_relevant.run(env, { topic: "anything" });
ok(hits.rows.length > 0, `whats_relevant -> ${hits.rows.length} hits`);
ok(Math.abs(hits.rows[0].score - 1.0) < 0.001, `self-match scores 1.0 (got ${hits.rows[0].score}) — blob alignment holds`);
ok(hits.rows[0].text === corpus.meta.rows[0].label, "top hit is the probed row itself");
ok(hits.rows.every((h, i, a) => i === 0 || a[i - 1].score >= h.score), "results ordered by score");
corpus.setProbe(corpus.meta.count - 1);
const last = await TOOLS.whats_relevant.run(env, { topic: "anything" });
ok(Math.abs(last.rows[0].score - 1.0) < 0.001, "alignment holds at the far end of the blob too");
const notesOnly = await TOOLS.notes_on.run(env, { topic: "anything" });
ok(notesOnly.rows.length > 0, "notes_on returns note-kind hits only");

console.log("\n── notes_on full:true (was read_note) ──");
corpus.setProbe(0);
const rn = await TOOLS.notes_on.run(env, { topic: "anything", full: true });
ok(rn.rows.length === 1, "returns exactly one note, never a list");
ok(typeof rn.rows[0].body === "string" && rn.rows[0].body.length > 0, `body returned (${rn.rows[0].body.length} chars)`);
ok(!!rn.rows[0].title && !!rn.rows[0].folder, "carries title + folder for context");
ok(JSON.stringify(rn).length <= MAX_BYTES, `within the 16KB cap (${JSON.stringify(rn).length})`);
ok(rn.note === undefined || rn.note.includes("truncated"), "truncation, if any, is announced");

// The default shape must NOT be the full one — the whole reason this is a flag
// rather than two tools is that the cheap answer stays the default.
const rnList = await TOOLS.notes_on.run(env, { topic: "anything" });
ok(rnList.rows.length > 1 && !("body" in rnList.rows[0]),
   `without full:true it lists titles (${rnList.rows.length}), no bodies`);

// no enumeration affordance
ok(!TOOLS.notes_on.schema.properties.id && !TOOLS.notes_on.schema.properties.offset,
   "no id/offset parameter — nothing to walk");

console.log("\n── new zones + join ──");
const ev = await TOOLS.events.run(env, {});
ok(Array.isArray(ev.rows), `events -> ${ev.rows.length} upcoming`);
const evFree = await TOOLS.events.run(env, { free: true });
ok(evFree.rows.every((r) => r.free === 1 || r.free === true), "events free filter holds");
const evTopic = await TOOLS.events.run(env, { topic: "film" });
ok(Array.isArray(evTopic.rows), `events topic filter -> ${evTopic.rows.length}`);

const tp = await TOOLS.taste_profile.run(env, {});
ok(tp.rows.length > 0, `taste_profile -> ${tp.rows.length} stated preferences`);
// Constraints PUBLISH (decided 2026-08-18). An assistant that recommends food
// without knowing about a severe nut allergy is not made safer by the omission.
// What must hold is that they arrive as CONSTRAINTS — typed apart from
// preferences, and present in the brief that every client loads unconditionally,
// because behind a tool call is behind a decision the client may never make.
const served = JSON.stringify(tp.rows).toLowerCase();
const cons = tp.rows.filter((r) => r.kind === "constraint");
ok(cons.length > 0, `constraints reachable (${cons.length})`);
ok(cons.some((r) => r.key === "allergies"), "allergies reachable, not held");
ok(tp.rows.some((r) => r.kind !== "constraint"),
   "preferences remain separable from constraints");

const briefText = await (await env.VECTORS.get("brief.md")).text();
const head = briefText.split("## Currently")[0];
ok(/##\s*Hard constraints/i.test(briefText), "brief states hard constraints");
ok(briefText.indexOf("Hard constraints") < briefText.indexOf("## Listening"),
   "constraints precede taste rather than trailing it");
ok(/peanut|tree nut/i.test(head), "the allergen is named");
ok(/severe/i.test(head), "severity stated, not just the allergen");
ok(/indian.*thai.*chinese|thai.*chinese/is.test(head),
   "avoided cuisines survive rendering uncut");
ok(!/…/.test(head), "constraints block is not truncated mid-list");
ok(!served.includes("constraints.json"), "no internal file layout disclosed");

const win = await TOOLS.around_the_time.run(env, { from: "2026-03-01", to: "2026-03-31" });
ok(Array.isArray(win.rows), `around_the_time -> ${win.rows.length} rows across sources`);
ok(new Set(win.rows.map((r) => r.kind)).size > 1 || win.note,
   "join spans more than one source (or explains why not)");
ok(JSON.stringify(win).length <= MAX_BYTES, "join within cap");

console.log("\n── ratings ──");
const rt = await TOOLS.ratings.run(env, {});
ok(rt.rows.length > 0, `ratings (all media) -> ${rt.rows.length}`);
ok(rt.rows.every((r) => r.scale), "every row states its scale");
ok(new Set(rt.rows.map((r) => r.medium)).size > 1, "spans more than one medium");
const films = await TOOLS.ratings.run(env, { medium: "films", min_rating: 4.5 });
ok(films.rows.every((r) => r.rating >= 4.5), "min_rating floor holds");
ok(films.rows.every((r) => r.scale === "0-5"), "films report the 0-5 scale");
const rest = await TOOLS.ratings.run(env, { medium: "restaurants" });
ok(rest.rows.every((r) => r.scale === "0-10"), "restaurants report 0-10, not 0-5");
ok(rest.rows.every((r) => r.rating > 0), "unrated rows excluded");

// the join must carry ratings, and must not let one medium starve the other
const yearWin = await TOOLS.around_the_time.run(env, { from: "2026-01-01", to: "2026-12-31" });
const kinds = new Set(yearWin.rows.map((r) => r.kind));
ok(kinds.has("film") && kinds.has("book"), "both film and book survive the window");
ok(yearWin.rows.filter((r) => r.kind === "film").some((r) => r.rating), "films carry a rating");

console.log("\n── taste verticals ──");
const tv = await TOOLS.taste_profile.run(env, {});
ok(tv.rows.some((r) => r.kind === "travel"), "travel vertical reachable");
ok(tv.rows.some((r) => r.kind === "events"), "events vertical still reachable");
const kindsSeen = new Set(tv.rows.map((r) => r.kind));
ok(kindsSeen.size >= 4, `spans ${kindsSeen.size} verticals: ${[...kindsSeen].join(", ")}`);

const ts = await TOOLS.taste_summary.run(env, {});
ok(!!ts.note, "listing without a kind explains itself rather than clipping");
const dining = await TOOLS.taste_summary.run(env, { kind: "dining" });
ok(dining.rows.length === 1, "one document returned");
ok(/median/i.test(dining.rows[0].text), "dining summary carries the scale calibration");
ok(JSON.stringify(dining).length <= MAX_BYTES, "document fits the cap uncut");

console.log("\n── reviews + links ──");
const rv = await TOOLS.reviews.run(env, {});
ok(rv.rows.length > 0, `reviews -> ${rv.rows.length}`);
ok(rv.rows.every((r) => r.review && r.review.length > 0), "every row carries the review text");
ok(rv.rows.every((r) => /letterboxd\.com|boxd\.it/.test(r.url || "")), "every review links out");
const topical = await TOOLS.reviews.run(env, { topic: "time" });
ok(Array.isArray(topical.rows), `topic search -> ${topical.rows.length}`);
const strong = await TOOLS.reviews.run(env, { min_rating: 4.5 });
ok(strong.rows.every((r) => parseFloat(r.rating) >= 4.5), "min_rating floor holds");

const fr = await TOOLS.ratings.run(env, { medium: "films" });
ok(fr.rows.every((r) => /boxd\.it|letterboxd/.test(r.url || "")), "rated films carry a link");
const rr = await TOOLS.ratings.run(env, { medium: "restaurants" });
ok(rr.rows.every((r) => r.url === undefined || r.url === null), "mediums without a link omit it cleanly");

console.log("\n── books are a library, not a reading log ──");
const bookCons = await TOOLS.consumption.run(env, { medium: "books" });
const bk = bookCons.rows[0];
ok(bk.total < 500, `books total counts finished only (${bk.total}, not the 920-row library)`);
ok(bk.to_read_backlog > 0, `to-read backlog reported separately (${bk.to_read_backlog})`);
ok(bk.total_label === "finished", "the total says what it counts");

const bratings = await TOOLS.ratings.run(env, { medium: "books" });
ok(bratings.rows.length > 0, `book ratings -> ${bratings.rows.length}`);

const brief2 = await (await env.VECTORS.get("brief.md")).text();
ok(!/920 books/.test(brief2), "brief no longer claims 920 books");
ok(/books read and .* shelved to-read/.test(brief2), "brief separates read from shelved");
ok(!/~0\/month/.test(brief2), "no rate quoted that the data cannot support");
ok(/finish date/.test(brief2), "brief states the finish-date undercount");

console.log("\n── counts say what they count ──");
const fCons = await TOOLS.consumption.run(env, { medium: "films" });
const bCons = await TOOLS.consumption.run(env, { medium: "beer" });
ok(bCons.rows[0].total_label === "check-ins", "beer total labelled as check-ins");
ok(bCons.rows[0].distinct_beers < bCons.rows[0].total, "distinct beers reported alongside");

// one row per film: the merge keyed on the permalink, and Letterboxd issues a
// different one per context, so a single watch counted up to three times.
const dupFilms = await TOOLS.ratings.run(env, { medium: "films" });
const titles = dupFilms.rows.map((r) => r.label.toLowerCase());
ok(new Set(titles).size === titles.length, "no duplicate films in a ratings page");

const b3 = await (await env.VECTORS.get("brief.md")).text();
ok(!/720 films/.test(b3), "brief no longer claims 720 films");
ok(/1,935 beers across 1,952 check-ins/.test(b3), "beer stated as beers vs check-ins");
ok(/115 written film reviews/.test(b3), "brief advertises the reviews, not just verdicts");

console.log("\n── collections (owning vs consuming) ──");
const col = await TOOLS.collection.run(env, {});
ok(col.rows.length > 0, `collection -> ${col.rows.length}`);
const vinyl = await TOOLS.collection.run(env, { kind: "vinyl" });
ok(vinyl.rows.every((r) => r.kind === "vinyl"), "kind filter holds");
ok(vinyl.rows.some((r) => r.acquired), "vinyl carries a purchase date");
const frag = await TOOLS.collection.run(env, { kind: "fragrance" });
ok(frag.rows.some((r) => r.thoughts), "fragrances carry their own notes");
ok(frag.rows.every((r) => !("genre" in r)), "empty columns dropped, not emitted as nulls");
const games = await TOOLS.collection.run(env, { kind: "board_game" });
// One game genuinely has no link in the source, so assert the shape of the
// links that exist rather than demanding every row have one.
ok(games.rows.filter((r) => r.url).every((r) => /boardgamegeek/.test(r.url)),
   "board-game links, where present, point at BGG");
ok(games.rows.filter((r) => r.url).length >= games.rows.length - 2,
   "at most a couple of games lack a link");
const colTopic = await TOOLS.collection.run(env, { topic: "rock" });
ok(Array.isArray(colTopic.rows), `topic search -> ${colTopic.rows.length}`);

console.log("\n── events: no source may dominate ──");
const ev2 = await TOOLS.events.run(env, {});
const mix = {};
for (const r of ev2.rows) mix[r.feed] = (mix[r.feed] || 0) + 1;
const feeds = Object.keys(mix).length;
ok(feeds >= 4, `spans ${feeds} sources, not one (${JSON.stringify(mix)})`);
ok(Math.max(...Object.values(mix)) <= 4, "no feed takes more than 4 of the answer");
ok(ev2.rows.some((r) => r.occurrences > 1), "recurring series collapsed with a count");
const evTitles = ev2.rows.map((r) => r.title);
ok(new Set(evTitles).size === evTitles.length, "no title repeated in one answer");
ok(ev2.rows.every((r) => r.start.slice(0, 10) >= new Date().toISOString().slice(0, 10)),
   "nothing already past");

console.log("\n── saves ──");
// No filter returns the AXES, not a flat sample: 20 arbitrary links out of
// 2,188 teaches an assistant nothing about what the library contains.
const facets = await TOOLS.saves.run(env, {});
ok(facets.rows.length === 0, "unfiltered call returns no links");
ok(facets.platforms.length > 0, `platforms offered (${facets.platforms.length})`);
ok(facets.collections.length > 0, `collections offered (${facets.collections.length})`);
ok(facets.total > 2000, `total stated (${facets.total})`);
ok(/\d{4}-\d{2}-\d{2} \.\. \d{4}-\d{2}-\d{2}/.test(facets.span), `span stated (${facets.span})`);
ok(facets.platforms.some((p) => p.name === "youtube"), "youtube is a first-class platform");

const yt = await TOOLS.saves.run(env, { platform: "youtube" });
ok(yt.rows.length > 0, `platform filter -> ${yt.rows.length}`);
ok(yt.rows.every((r) => r.platform === "youtube"), "platform filter holds");
ok(yt.rows.every((r) => /youtube\.com|youtu\.be/.test(r.url)), "and the urls agree");

const provoking = await TOOLS.saves.run(env, { collection: "thought-provoking" });
ok(provoking.rows.every((r) => r.collection.toLowerCase() === "thought-provoking"), "collection filter holds");

const recent = await TOOLS.saves.run(env, { since: "2026-08-01" });
ok(recent.rows.every((r) => r.saved >= "2026-08-01"), "since filter holds");

const tagged = await TOOLS.saves.run(env, { tag: "substack" });
ok(tagged.rows.every((r) => /substack/i.test(r.tags || "")), "tag filter holds");
ok(tagged.rows.every((r) => !/, [A-Za-z], /.test(r.tags || "")), "tags are words, not characters");

console.log("\n── conversation topics ──");
const rtop = await TOOLS.recent_topics.run(env, {});
ok(rtop.rows.length > 0, `recent_topics -> ${rtop.rows.length}`);
ok(rtop.rows.every((r) => r.title && r.turns > 0), "each row carries a title and volume");
ok(rtop.rows.every((r) => r.text === undefined && r.body === undefined),
   "no transcript text is returned, only topics");
const heavy = await TOOLS.recent_topics.run(env, { min_turns: 50 });
ok(heavy.rows.every((r) => r.turns >= 50), "min_turns floor holds");

// the three named conversations must not be reachable by any route
const HELD = ["Emergent relationship anarchy", "Group dating", "relationship compatibility"];
const all = JSON.stringify(await TOOLS.recent_topics.run(env, {})).toLowerCase();
for (const h of HELD) ok(!all.includes(h.toLowerCase()), `held: ${h.slice(0, 28)}`);
const searched = JSON.stringify(await TOOLS.recent_topics.run(env, { topic: "relationship" })).toLowerCase();
ok(!searched.includes("nonmonogamy") && !searched.includes("group dating"),
   "held topics stay held even when searched for directly");

console.log("\n── television ──");
const tvc = await TOOLS.consumption.run(env, { medium: "tv" });
ok(tvc.rows[0].total > 300, `tv shows -> ${tvc.rows[0].total}`);
ok(tvc.rows[0].episodes_watched > 7000, `episodes reported separately (${tvc.rows[0].episodes_watched})`);
ok(tvc.rows[0].total_label === "shows", "the total says it counts shows, not episodes");
const b4 = await (await env.VECTORS.get("brief.md")).text();
ok(/tv shows/.test(b4), "brief mentions television at all");

console.log("\n── events: date range ──");
const wknd = await TOOLS.events.run(env, { from: "2026-08-22", to: "2026-08-23" });
ok(wknd.rows.length > 0, `weekend window -> ${wknd.rows.length}`);
ok(wknd.rows.every((r) => r.start.slice(0,10) >= "2026-08-22" && r.start.slice(0,10) <= "2026-08-23"),
   "every row falls inside the requested window");
const past = await TOOLS.events.run(env, { from: "2020-01-01", to: "2020-12-31" });
ok(past.rows.length === 0, "a window entirely in the past returns nothing, not the future");
const dflt = await TOOLS.events.run(env, {});
ok(dflt.rows.every((r) => r.start.slice(0,10) >= new Date().toISOString().slice(0,10)),
   "omitting from- still never volunteers the past");

console.log("\n── thread text ──");
const th = await TOOLS.thread.run(env, { topic: "mp3" });
ok(th.rows.length === 1, "one thread returned");
const t0 = th.rows[0];
ok(Array.isArray(t0.his_words) && t0.his_words.length > 0, `owner turns returned (${t0.his_words?.length})`);
ok(typeof t0.landed === "string", "conclusion included by default");
const concl = (await TOOLS.thread.run(env, { topic: "mp3", include: "conclusion" })).rows[0];
ok(concl.his_words === undefined, "include=conclusion omits the raw turns");
const raw = (await TOOLS.thread.run(env, { topic: "mp3", include: "turns" })).rows[0];
ok(raw.landed === undefined, "include=turns omits the conclusion");
ok(JSON.stringify(th).length <= MAX_BYTES, "thread fits the cap");

// the assistant's side must never be retrievable, and held threads stay held
const allText = JSON.stringify(th).toLowerCase();
ok(!/i'd be happy to|as an ai|let me know if/.test(allText), "no assistant prose in the payload");
const heldThread = await TOOLS.thread.run(env, { topic: "nonmonogamy" });
ok(heldThread.rows.length === 0, "held threads unreachable by the thread tool too");

console.log("\n── backlog ──");
const bkFacets = await TOOLS.backlog.run(env, {});
ok(bkFacets.rows.length === 0 && bkFacets.kinds?.length === 4, "no kind -> the four piles, not arbitrary rows");
ok(bkFacets.kinds.every((k) => k.n > 0), `every pile is non-empty (${bkFacets.kinds.map((k) => `${k.kind}:${k.n}`).join(" ")})`);
ok(/letterboxd/i.test(bkFacets.gap || ""), "the missing watchlist is declared, not silently absent");

const toRead = await TOOLS.backlog.run(env, { kind: "read" });
ok(toRead.rows.length > 0 && toRead.rows.every((r) => r.shelf === "to-read"), `read -> ${toRead.rows.length} to-read`);
ok(toRead.rows.every((r) => r.title && r.queued), "each row carries a title and a queued date");
ok(toRead.rows.every((r) => /^\d{4}-\d{2}-\d{2}$/.test(r.queued)), "both Goodreads date shapes normalise to ISO days");
ok(toRead.rows.every((r) => r.my_rating === undefined), "no unread book carries an owner rating");
ok(!!toRead.gap && !!toRead.scope, "kind results still declare scope and the watch gap");

const resume = await TOOLS.backlog.run(env, { kind: "resume" });
ok(resume.rows.length > 0 && resume.rows.every((r) => r.shelf !== "to-read"), `resume -> started-but-unfinished only`);

// the whole point of the tool: reach what has been sitting, not just the newest
const newest = await TOOLS.backlog.run(env, { kind: "read" });
const oldest = await TOOLS.backlog.run(env, { kind: "read", order: "oldest" });
ok(oldest.rows[0].queued < newest.rows[0].queued, `order=oldest digs (${oldest.rows[0].queued} vs ${newest.rows[0].queued})`);
ok(newest.rows.every((r, i, a) => i === 0 || a[i - 1].queued >= r.queued), "default order is newest-first");

const sinceBk = await TOOLS.backlog.run(env, { kind: "read", since: "2026-01-01" });
ok(sinceBk.rows.every((r) => r.queued >= "2026-01-01"), "since filter holds across both date shapes");
const topicBk = await TOOLS.backlog.run(env, { kind: "read", topic: "the" });
ok(topicBk.rows.every((r) => /the/i.test(`${r.title} ${r.author ?? ""}`)), "topic matches title or author");

const make = await TOOLS.backlog.run(env, { kind: "make" });
ok(make.rows.length > 0 && make.rows.every((r) => r.collection === "want-to-make"), `make -> ${make.rows.length} from want-to-make`);
ok(/unfiled/.test(make.notes || ""), "make admits it undercounts rather than reporting 9 as the whole truth");
const buy = await TOOLS.backlog.run(env, { kind: "buy" });
ok(buy.rows.every((r) => ["Gift Ideas", "Shopping"].includes(r.collection)), "buy spans both purchase collections");

const bogus = await TOOLS.backlog.run(env, { kind: "watch" });
ok(bogus.rows.length === 0 && !!bogus.error, "an unknown kind errors rather than returning a wrong pile");
ok(JSON.stringify(await TOOLS.backlog.run(env, { kind: "read" })).length <= MAX_BYTES + 400, "backlog within cap");

console.log("\n── summaries are reachable, not just present ──");
const listed = await TOOLS.recent_topics.run(env, { min_turns: 40 });
ok(listed.rows.some((r) => r.gist && r.gist.length > 0),
   "browsing threads surfaces a gist, not just titles");
const full = await TOOLS.thread.run(env, { topic: listed.rows.find((r) => r.gist)?.title || "mp3" });
ok(!!full.rows[0].summary, "thread returns the full summary");
ok(/not the owner's words/.test(full.rows[0].summary_is || ""), "summary is attributed as machine-written");
ok(typeof full.rows[0].landed === "string", "the verbatim it can be checked against ships with it");
const b5 = await (await env.VECTORS.get("brief.md")).text();
ok(/distillation/i.test(b5), "brief tells an assistant the distillations exist");

console.log("\n── dialogue mode ──");
const dlg = await TOOLS.thread.run(env, { topic: "mp3", include: "dialogue" });
const d0 = dlg.rows[0];
ok(Array.isArray(d0.dialogue) && d0.dialogue.length > 0, `dialogue -> ${d0.dialogue?.length} lines`);
ok(d0.dialogue.some((l) => l.startsWith("ada:")), "owner lines are tagged");
ok(d0.dialogue.some((l) => l.startsWith("assistant:")), "the other side is present");
ok(/not the owner's words/.test(d0.dialogue_note || ""),
   "assistant lines are labelled as not the owner's");
ok(JSON.stringify(dlg).length <= MAX_BYTES, "dialogue respects the cap");
const hisOnly = (await TOOLS.thread.run(env, { topic: "mp3", include: "turns" })).rows[0];
ok(hisOnly.dialogue === undefined, "include=turns still returns the owner side only");
const heldD = await TOOLS.thread.run(env, { topic: "nonmonogamy", include: "dialogue" });
ok(heldD.rows.length === 0, "held threads stay held in dialogue mode");

console.log("\n── the cap must never eat the whole answer ──");
// A thread big enough to fill the budget used to assemble a 16,665-byte row
// against a 16,384 cap, whereupon cap() returned zero rows and told the caller
// to narrow a question that was already one thread.
const biggest = (await env.DB.prepare(
  `SELECT title, sum(length(COALESCE(text,''))) AS chars FROM t0_chat
   GROUP BY title ORDER BY chars DESC LIMIT 1`).bind().all()).results[0];
ok(biggest.chars > MAX_BYTES, `probing the largest thread (${biggest.chars} chars, > the cap)`);
for (const inc of ["dialogue", "turns", "both"]) {
  const r = await TOOLS.thread.run(env, { topic: biggest.title, include: inc });
  const bytes = JSON.stringify(r).length;
  ok(r.rows.length === 1, `include=${inc} returns the thread, not an empty answer`);
  ok(bytes <= MAX_BYTES, `include=${inc} fits the cap (${bytes} <= ${MAX_BYTES})`);
  const listed = r.rows[0]?.dialogue ?? r.rows[0]?.his_words;
  if (inc !== "conclusion") ok(listed?.length > 0, `include=${inc} carries turns, not just metadata`);
}
// cap() must still refuse to emit an oversized row — the fix is that callers
// budget correctly, not that the cap got softer.
const oversized = cap([{ blob: "z".repeat(MAX_BYTES + 100) }]);
ok(oversized.rows.length === 0, "an oversized row is still withheld, not emitted");
ok(/did not budget/.test(oversized.note), "and is reported as a tool bug, not as a question to narrow");

console.log("\n── the window must not starve its last source ──");
// 8+5+4+4+4 limits against a 20-row cap: concatenating meant books lost a row
// and television lost all four, in every month that filled every branch.
const fullMonth = await TOOLS.around_the_time.run(env, { from: "2026-03-01", to: "2026-03-31" });
const seenKinds = new Set(fullMonth.rows.map((r) => r.kind));
for (const k of ["note", "artist", "film", "book", "tv"]) {
  const present = (await env.DB.prepare(
    k === "note"   ? `SELECT count(*) n FROM t1_notes WHERE substr(created,1,10) BETWEEN ? AND ?`
    : k === "artist" ? `SELECT count(*) n FROM t0_music WHERE substr(created,1,10) BETWEEN ? AND ?`
    : k === "film" ? `SELECT count(*) n FROM t0_film WHERE substr(created,1,10) BETWEEN ? AND ?`
    : k === "book" ? `SELECT count(*) n FROM t0_book WHERE shelf='read' AND substr(created,1,10) BETWEEN ? AND ?`
    : `SELECT count(*) n FROM t0_tv WHERE substr(created,1,10) BETWEEN ? AND ?`)
    .bind("2026-03-01", "2026-03-31").all()).results[0].n;
  if (present > 0) ok(seenKinds.has(k), `${k} survives a window where every source has data`);
}

console.log("\n── a collapsed series must describe ONE occurrence ──");
// min(venue)/min(url) per column is lexicographic, not the soonest row: 16 of
// 117 upcoming series showed one occurrence's date beside another's link.
const evs = await TOOLS.events.run(env, {});
let checked = 0;
for (const r of evs.rows) {
  const inst = (await env.DB.prepare(
    `SELECT venue, url, location FROM t0_event WHERE title = ? AND feed = ? AND start = ?`)
    .bind(r.title, r.feed, r.start).all()).results;
  if (!inst.length) continue;
  checked++;
  const matches = inst.some((i) => (i.url ?? null) === (r.url ?? null) && (i.venue ?? null) === (r.venue ?? null));
  if (!matches) ok(false, `${r.title.slice(0, 40)}: shows a venue/url from a different date`);
}
ok(checked > 0, `checked ${checked} collapsed rows against their own occurrence`);
ok(evs.rows.every((r) => r.price_varies == null || r.price_varies === 1),
   "price_varies is a flag or absent, never a stray value");
const mixed = (await env.DB.prepare(
  `SELECT title, feed FROM t0_event WHERE substr(start,1,10) >= date('now')
   GROUP BY title, feed HAVING count(*) > 1 AND min(free) <> max(free) LIMIT 1`).bind().all()).results[0];
if (mixed) {
  const r = await TOOLS.events.run(env, { topic: mixed.title.toLowerCase().slice(0, 12) });
  const row = r.rows.find((x) => x.title === mixed.title);
  ok(!row || row.price_varies === 1, "a series with mixed pricing says so rather than reporting the free one");
} else {
  ok(true, "no mixed-price series upcoming to check");
}

console.log("\n── caller observability (phase 1 of origin gating) ──");
// A real Request has no `cf` in Node; the edge fills it in production. Both
// shapes are exercised, because the empty one is what the test harness and any
// non-Cloudflare caller produce and it must not throw.
const withCf = (body, token, cf, ua = "poke/1.0") => {
  const r = new Request("https://x/", {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json", "user-agent": ua,
               "cf-connecting-ip": cf?.ip ?? "203.0.113.7" },
    body: JSON.stringify(body),
  });
  if (cf) Object.defineProperty(r, "cf", { value: cf, configurable: true });
  return worker.fetch(r, env);
};
// Scoped by ip: earlier sections of this file already drove traffic through
// worker.fetch, so the table is not empty by the time we get here.
const callers = async (ip = "203.0.113.7") =>
  (await env.DB.prepare("SELECT * FROM wh_callers WHERE ip = ?").bind(ip).all()).results;
const ping = { jsonrpc: "2.0", id: 1, method: "ping" };
const POKE = { ip: "203.0.113.7", asn: 396982, asOrganization: "Google Cloud", country: "US", colo: "EWR" };

await withCf(ping, "test-token", POKE);
let rows = await callers();
const okRow = rows.find((r) => r.outcome === "ok");
ok(!!okRow, "an authorised call is recorded");
ok(okRow.asn === 396982 && okRow.org === "Google Cloud", `asn + org captured (${okRow.asn} ${okRow.org})`);
ok(okRow.ip === "203.0.113.7" && okRow.country === "US" && okRow.colo === "EWR", "ip, country, colo captured");
ok(okRow.ua === "poke/1.0", "the client names itself");
ok(okRow.first_seen && okRow.last_seen, "both ends of the window recorded");

await withCf(ping, "test-token", POKE);
rows = await callers();
ok(rows.filter((r) => r.outcome === "ok").length === 1, "a repeat call increments rather than appending a row");
ok(rows.find((r) => r.outcome === "ok").n === 2, "the counter moved");

const denied = await withCf(ping, "wrong-token-x", { ...POKE, ip: "198.51.100.9" }, "curl/8");
ok(denied.status === 401, "a wrong token is still rejected");
rows = await callers("198.51.100.9");
const badRow = rows.find((r) => r.outcome === "denied");
ok(!!badRow && badRow.ip === "198.51.100.9", "the rejected caller is on the record too");
const allRows = (await env.DB.prepare("SELECT * FROM wh_callers").bind().all()).results;
ok(JSON.stringify(allRows).includes("wrong-token-x") === false, "the presented token is never stored");
ok(JSON.stringify(allRows).includes("test-token") === false, "nor is the real one");

// Rolling up bounds rows; the throttle is what bounds WRITES on the path anyone
// can reach. Same ip, same minute -> no second write.
const before = (await callers("198.51.100.9")).find((r) => r.outcome === "denied").n;
await withCf(ping, "wrong-again", { ...POKE, ip: "198.51.100.9" }, "curl/8");
const after = (await callers("198.51.100.9")).find((r) => r.outcome === "denied").n;
ok(after === before, "repeated denials from one ip inside a minute cost one write, not one each");

// Nothing above may cost the surface its answer.
const stillWorks = await (await withCf({ jsonrpc: "2.0", id: 9, method: "tools/list" }, "test-token", POKE)).json();
ok(stillWorks.result?.tools?.length > 0, "observation does not disturb the answer");

// The empty-cf path: a caller Cloudflare knows nothing about must log, not throw.
const bare = await post(ping);
ok(bare.status === 200, "a request with no cf metadata still succeeds");
ok((await callers("")).some((r) => r.asn === 0), "and is recorded with the facts blank rather than dropped");

// The audit log must agree with its own DDL — a mismatch fails silently inside
// the try/catch, so the surface would answer normally and log nothing at all.
await withCf({ jsonrpc: "2.0", id: 4, method: "resources/read", params: { uri: "warehouse://brief" } }, "test-token", POKE);
const audited = (await env.DB.prepare("SELECT * FROM wh_audit").bind().all()).results;
ok(audited.length > 0, `wh_audit still receives writes (${audited.length})`);
ok(audited.every((r) => "ip" in r && "asn" in r), "every audit row carries the caller, not just the call");
ok(audited.at(-1).ip === "203.0.113.7", "and it is the caller's ip, correlatable with wh_callers");

console.log("\n── projects (ADR-0011) ──");
const pAll = await TOOLS.projects.run(env, {});
ok(pAll.statuses?.length > 0, "no-filter call returns the status bands");
ok(pAll.rows.length > 0 && "commits_90d" in pAll.rows[0],
   "and ranks by commits in the window, not by last-commit date");
// Whichever repo the bundle actually holds — naming one here would tie the
// engine's tests to one person's workshop.
const anyRepo = (await env.DB.prepare("SELECT name FROM t1_project LIMIT 1").bind().all())
  .results[0]?.name;
const pWh = await TOOLS.projects.run(env, { topic: anyRepo });
ok(pWh.rows.some((r) => r.name === anyRepo), "topic matches a repo by name");
ok(pWh.rows.every((r) => !("local_path" in r)),
   "the on-disk path is not part of the answer");
const pStalled = await TOOLS.projects.run(env, { status: "dormant" });
ok(pStalled.rows.every((r) => r.status === "dormant"), "status filter holds");

const aAll = await TOOLS.project_activity.run(env, {});
ok(aAll.window?.from && aAll.window?.to, "activity states the window it answered for");
const aOne = await TOOLS.project_activity.run(env, { repo: anyRepo, from: "2020-01-01" });
ok(aOne.rows.length > 0 && aOne.rows.every((r) => r.subject && r.date),
   "a named repo returns dated commit subjects");
ok(aOne.rows.every((r) => !("sha" in r)), "and not shas, which no reader can use");

const dSearch = await TOOLS.project_docs.run(env, { topic: anyRepo });
ok(dSearch.rows.length > 0 && dSearch.rows.every((r) => "excerpt" in r && !("body" in r)),
   "docs search returns excerpts, not whole bodies");
// A repo that actually HAS a context doc — not every repo does, and asserting
// against one that does not tests the fixture rather than the tool.
// The SHORTEST context doc, because `full:true` withholds a row that blows the
// 16KB cap — picking an arbitrary repo tests the cap, not the tool.
const ctxRepo = (await env.DB.prepare(
  "SELECT repo FROM t1_project_doc WHERE kind = 'context' ORDER BY length(body) ASC LIMIT 1")
  .bind().all()).results[0]?.repo;
const dFull = await TOOLS.project_docs.run(env, { repo: ctxRepo, kind: "context", full: true });
ok(dFull.rows.length === 1 && dFull.rows[0].body?.length > 0,
   "full=true returns one whole document");

const oAll = await TOOLS.project_open.run(env, {});
ok(oAll.rows.every((r) => "open_items" in r), "open work with no repo returns where it is concentrated");
const oOne = await TOOLS.project_open.run(env, { repo: anyRepo });
ok(oOne.rows.every((r) => r.text && r.path), "and per-repo items carry path + marker text");

// The line that matters most: prose crossed, source files did not. The real
// guarantee is at the SCAN, not the filter — only markdown is ever read — so
// that is what this asserts. Fenced samples inside a README are part of the
// prose and are meant to be here; a .py file is not, at any percentage.
const docPaths = (await env.DB.prepare("SELECT DISTINCT path FROM t1_project_doc").bind().all()).results;
ok(docPaths.length > 0, `project docs published (${docPaths.length} files)`);
ok(docPaths.every((r) => r.path.toLowerCase().endsWith(".md")),
   "every published project document is markdown — no source file is in the store");
const openPaths = (await env.DB.prepare(
  "SELECT count(*) AS n FROM t1_project_open WHERE length(text) > 320").bind().all()).results;
ok(openPaths[0].n === 0, "marker rows carry a line of text, never a file");

console.log("\n── the published blog ──");
const allPosts = (await env.DB.prepare(
  "SELECT slug, title, url, kind, body FROM t1_post").bind().all()).results;
ok(allPosts.length > 0, `blog published (${allPosts.length} posts)`);

// The link is the whole point of this zone, so it gets asserted at the source
// rather than trusted. A row whose url does not derive from its own slug sends
// a reader to someone else's post, or to a 404 — a wrong link is worse than no
// link, because it looks like an answer.
// Asserted against each row's OWN origin rather than a hostname written here:
// the harness runs against whatever instance built the bundle, and a test that
// names one person's blog only passes on one person's laptop.
ok(allPosts.every((r) => r.url === `${new URL(r.url).origin}/posts/${r.slug}/`),
   "every post url is exactly the permalink its slug resolves to");
ok(allPosts.every((r) => r.title && r.slug), "no post is missing a title or a slug");
const postIds = (await env.DB.prepare("SELECT id FROM t1_post").bind().all()).results;
ok(new Set(postIds.map((r) => r.id)).size === postIds.length,
   "post ids are unique — the vector join key cannot amplify");

// Find a post that actually has a vector, and probe with its own embedding:
// this asserts the post vectors are in the SAME blob and aligned, which is the
// only thing standing between a search and a confidently wrong link.
const postIdx = corpus.meta.rows.findIndex((r) => r.kind === "post");
ok(postIdx >= 0, "post vectors reached the blob");
if (postIdx >= 0) {
  corpus.setProbe(postIdx);
  const pr = await TOOLS.posts.run(env, { topic: "anything" });
  ok(pr.rows.length > 0, `posts -> ${pr.rows.length} hits`);
  ok(Math.abs(pr.rows[0].score - 1.0) < 0.001,
     `post self-match scores 1.0 (got ${pr.rows[0].score}) — post block is aligned`);
  const known = new Map(allPosts.map((r) => [r.url, r.title]));
  ok(pr.rows.every((r) => known.get(r.url) === r.title),
     "every returned link resolves to the published post it is labelled with");

  // The kind filter over-fetches deliberately; assert it actually narrows AND
  // still returns something, since a filter that always returns empty passes a
  // naive "every row matches" check trivially.
  const arts = await TOOLS.posts.run(env, { topic: "anything", kind: "article" });
  ok(arts.rows.length > 0 && arts.rows.every((r) => r.kind === "article"),
     `posts filters by kind (${arts.rows.length} articles)`);

  const rp = await TOOLS.posts.run(env, { topic: "anything", full: true });
  ok(rp.rows.length === 1 && rp.rows[0].body?.length > 0, "posts full:true returns one whole post");
  ok(/^https?:\/\/[^/]+\/posts\//.test(rp.rows[0].url || ""),
     "and carries the live link beside the text");
  ok(JSON.stringify(rp).length <= MAX_BYTES + 200, "the full post stays within cap");

  // whats_relevant is the entry point; a post hit there must carry its link too
  // or the assistant has to know to call a second tool to get one.
  const wr = await TOOLS.whats_relevant.run(env, { topic: "anything" });
  const ph = wr.rows.filter((r) => r.kind === "post");
  ok(ph.length > 0 && ph.every((r) => r.url?.startsWith("https://example.com/posts/")),
     "whats_relevant hands back the URL on post hits");
}
ok(!TOOLS.posts.schema.properties.offset && !TOOLS.posts.schema.properties.id,
   "the blog tool exposes no cursor and no id (ADR-0007)");

console.log("\n── the item spine ──");
const x_ag = await TOOLS.agenda.run(env, {});
ok(x_ag.rows.length > 0, `agenda -> ${x_ag.rows.length} live items`);
// The families use different status vocabularies, and filtering on one would
// silently empty the others. That is the specific bug this asserts against.
const x_fams = new Set(x_ag.rows.map((r) => r.family));
ok(x_fams.size > 1, `more than one family is live (${[...x_fams].join(", ")})`);
ok(!x_ag.rows.some((r) => r.status === "done"), "finished tasks are excluded by default");
const x_agDone = await TOOLS.agenda.run(env, { include_done: true });
ok(x_agDone.rows.length >= x_ag.rows.length, "include_done widens rather than narrows");
const x_agHabit = await TOOLS.agenda.run(env, { family: "habit" });
ok(x_agHabit.rows.length > 0 && x_agHabit.rows.every((r) => r.family === "habit"),
   `family filter holds (${x_agHabit.rows.length} habits)`);
ok(typeof x_ag.shape === "string" && x_ag.shape.includes("/"),
   "carries the full shape, so a capped list is not read as the whole spine");
ok((x_ag.note ?? "").includes("read-only"),
   "says completion is not available here — ADR-0006 stated, not implied");

const x_hist = await TOOLS.history.run(env, {});
ok(x_hist.rows.length > 0, `history -> ${x_hist.rows.length} events`);
ok(x_hist.rows.every((r) => r.item && r.when_), "every event names its item and when it happened");

console.log("\n── recipes ──");
const x_rc = await TOOLS.recipes.run(env, {});
ok(x_rc.rows.length > 0, `recipes -> ${x_rc.rows.length}`);
ok(x_rc.rows.every((r) => r.source_url?.startsWith("http")), "each links to where it was published");
const x_rcFull = await TOOLS.recipes.run(env, { full: true });
ok(x_rcFull.rows.length === 1, "full:true returns exactly one");
ok(Array.isArray(x_rcFull.rows[0].ingredients) && Array.isArray(x_rcFull.rows[0].steps),
   "ingredients and steps are parsed, not JSON strings on the wire");

console.log("\n── medium ──");
const x_dir = await TOOLS.medium.run(env, {});
ok(x_dir.rows.length >= 5 && x_dir.rows.every((r) => r.medium && r.unit),
   `no name returns the directory (${x_dir.rows.length} media)`);
const x_film = await TOOLS.medium.run(env, { name: "film" });
ok(x_film.rows.length === 1, "one medium, one row");
const x_f = x_film.rows[0];
ok(x_f.consumed > 0 && x_f.rated?.n > 0, `film: ${x_f.consumed} watched, ${x_f.rated.n} rated`);
ok(x_f.rated.scale === "0-5", "the scale rides with the numbers");
ok(x_f.written?.reviews > 0 && x_f.owns?.dvd > 0, "joins criticism and shelf in one call");
ok((x_film.note ?? "").includes("watchlist"), "carries the watchlist gap");
// The calibration warning is the reason this tool is worth having for dining:
// an 8 on a 0-10 scale whose median is 8.1 is not praise.
const x_dining = await TOOLS.medium.run(env, { name: "restaurant" });
ok(x_dining.rows[0].rated.scale === "0-10" && (x_dining.note ?? "").includes("taste_summary"),
   "restaurant points at its own calibration before its numbers can be misread");
const x_music = await TOOLS.medium.run(env, { name: "music" });
ok(!x_music.rows[0].rated && (x_music.note ?? "").includes("does not rate"),
   "an unrated medium says so rather than returning an empty rating block");
const x_bogus = await TOOLS.medium.run(env, { name: "opera" });
ok(x_bogus.rows.length >= 5 && (x_bogus.note ?? "").includes("no medium"),
   "an unknown medium returns the directory and says why");

console.log("\n── drafts ──");
{
  const all = await TOOLS.drafts.run(env, {});
  const n = (await env.DB.prepare("SELECT count(*) AS n FROM t1_draft").bind().all()).results[0].n;
  ok(all.rows.length === Math.min(n, MAX_ROWS), `drafts -> ${all.rows.length} of ${n}`);
  if (n === 0) {
    // Zero drafts is a real state, not a failure — but it must be distinguishable
    // from "your filter matched nothing", or a reader concludes nothing was ever written.
    ok((all.note ?? "").includes("no drafts in progress"), "an empty desk says so plainly");
    const filtered = await TOOLS.drafts.run(env, { topic: "nothing" });
    ok((filtered.note ?? "").includes("may still have other drafts"),
       "and a filter that matched nothing says something different");
  } else {
    ok(all.rows.every((r) => r.title && r.modified), "each carries a title and when it moved");
    ok(all.rows.every((r) => typeof r.days_since_touched === "number"),
       "and how long it has sat — the question a writer cannot answer about themselves");
    const one = await TOOLS.drafts.run(env, { full: true });
    ok(one.rows.length === 1 && typeof one.rows[0].body === "string",
       "full:true returns one whole draft");
    ok(JSON.stringify(one).length <= MAX_BYTES + 200, "and stays within cap");
    const cold = await TOOLS.drafts.run(env, { stale_days: 3650 });
    ok(cold.rows.length === 0, "stale_days filters rather than being ignored");
  }
  ok(!TOOLS.drafts.schema.properties.id && !TOOLS.drafts.schema.properties.offset,
     "no cursor, no id (ADR-0007)");
}

console.log("\n── taxonomy (ADR-0015) ──");
{
  // A facet nothing checks is a facet that drifts, and an unclassified tool is
  // the one that gets called for the wrong reason. The vocabularies are closed
  // on purpose: widening one should be an edit to tools.js and an argument in
  // the ADR, not a typo that quietly invents a ninth class.
  const entries = Object.entries(TOOLS);
  const unfiled = entries.filter(([, t]) => !t.class || !t.domain || !t.kind).map(([n]) => n);
  ok(unfiled.length === 0, `every tool declares class, domain and kind${unfiled.length ? " — missing: " + unfiled : ""}`);

  const bad = entries.filter(([, t]) =>
    !CLASSES.includes(t.class) || !DOMAINS.includes(t.domain) || !KINDS.includes(t.kind)).map(([n]) => n);
  ok(bad.length === 0, `every facet value is in the closed vocabulary${bad.length ? " — offenders: " + bad : ""}`);

  // The one implication the taxonomy asserts: a lens owns no rows of its own,
  // so it cannot be fixed to a single domain. If a lens ever gets a domain, it
  // stopped being a lens and became a tool that should say which zone it reads.
  const fixedLens = entries.filter(([, t]) => t.class === "lens" && t.domain !== "*").map(([n]) => n);
  ok(fixedLens.length === 0, `class:lens implies domain:"*"${fixedLens.length ? " — offenders: " + fixedLens : ""}`);

  // The facets are internal. tools/list is the MCP contract and must carry
  // exactly name, description and inputSchema — leaking our filing system into
  // it would make every client's tool descriptor depend on our bookkeeping.
  const listed = (await post({ jsonrpc: "2.0", id: 1, method: "tools/list" }).then((r) => r.json()))
    .result.tools;
  ok(listed.every((t) => Object.keys(t).sort().join() === "description,inputSchema,name"),
     "tools/list still exposes name, description and inputSchema only");
}

console.log("\n── documentation ──");
{
  // The README table is generated from TOOLS. This is the check that keeps it
  // honest: the hand-maintained version listed read_note and read_post for a
  // while after they stopped existing, and nothing noticed, because a doc that
  // describes the surface from outside the surface has nothing pulling it
  // forward. Same failure as the handoff plan that had to be deleted.
  const { table } = await import("../scripts/gen-tool-table.mjs");
  const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
  ok(readme.includes(table()), "README tool table matches TOOLS exactly");

  const names = Object.keys(TOOLS);
  ok(names.every((n) => readme.includes(`\`${n}\``)), "every tool is documented");
  const documented = [...readme.matchAll(/^\| `([a-z_]+)`/gm)].map((m) => m[1]);
  const ghosts = documented.filter((n) => !names.includes(n));
  ok(ghosts.length === 0, `no tool is documented that does not exist${ghosts.length ? ": " + ghosts : ""}`);

  // A tool nobody is told about is a tool nobody calls. The brief is the only
  // thing a client reads unprompted, so a capability missing from it is
  // effectively unshipped — this has now happened four times.
  // Same base the harness uses: the bundle belongs to an instance, not to the
  // engine checkout this file happens to sit in.
  const briefPath = process.env.EXO_HOME
    ? new URL(`file://${process.env.EXO_HOME}/zones/_serve/brief.md`)
    : new URL("../../zones/_serve/brief.md", import.meta.url);
  const brief = readFileSync(briefPath, "utf8");
  const SHOULD_ADVERTISE = ["agenda", "recipes", "medium", "backlog", "around_the_time", "drafts"];
  const unadvertised = SHOULD_ADVERTISE.filter((n) => !brief.includes(n));
  ok(unadvertised.length === 0, `the brief names the tools it should${unadvertised.length ? " — missing: " + unadvertised : ""}`);
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
