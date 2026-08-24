/**
 * Exercises the real worker modules against real published data.
 * Stubs only the three Cloudflare bindings: D1 -> node:sqlite, R2 -> the bundle
 * on disk, Workers AI -> a vector lifted from the corpus itself.
 */
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// The bundle belongs to an instance, not to the engine, so the harness looks
// where the engine looks: EXO_HOME if it is set, and the enclosing checkout
// otherwise. Without this, CI could only ever test the bundle of whoever's
// laptop the repo happened to be cloned onto.
const CF = process.env.EXO_HOME
  ? path.resolve(process.env.EXO_HOME, "zones/_serve/cf")
  : path.resolve(fileURLToPath(import.meta.url), "../../../zones/_serve/cf");

// ── D1 stub ──────────────────────────────────────────────────────────────────
const db = new DatabaseSync(":memory:");
db.exec(readFileSync(path.join(CF, "schema.sql"), "utf8"));
for (const f of readdirSync(path.join(CF, "data"))) {
  db.exec(readFileSync(path.join(CF, "data", f), "utf8"));
}

// Columns the current code selects and this bundle does not carry. A published
// bundle is a snapshot: add a column to a zone and every fixture built before
// that moment is missing it until it is republished. SQLite only notices at
// prepare() time, which is inside a tool call, which is inside the run — so the
// first version of this file let one stale column throw and take every later
// case with it. `t1_notes.uuid` did exactly that after ADR-0020 added it, and
// the surviving symptom was a run that ended at case 40 of 300 with a SQL
// error, reported as though the SQL were wrong.
//
// So: add what is missing, empty, and say so at the end. The assertion that
// genuinely depends on the column then fails on its own terms — visibly, as one
// case — and the several hundred that do not still run. Same argument as the
// zone shims in sqlcheck.mjs: an incomplete fixture must not quietly decide
// which tests happen.
export const DRIFT = new Set();

const tablesIn = (sql) =>
  [...sql.matchAll(/\b(?:from|join)\s+([a-z_][\w]*)/gi)].map((m) => m[1]);

const columnsOf = (table) =>
  new Set(db.prepare(`PRAGMA table_info(${table})`).all().map((r) => r.name));

const tableExists = (table) =>
  db.prepare("SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name=?").get(table).n > 0;

function prepareShimmed(sql) {
  // Bounded: every pass either adds a column or rethrows, so the only way to
  // spin is a bundle missing more columns than any query names.
  for (let attempt = 0; attempt < 32; attempt++) {
    try {
      return db.prepare(sql);
    } catch (err) {
      const missing = /no such column:\s*(?:[\w]+\.)?([\w]+)/.exec(err.message);
      if (!missing) throw err;
      const col = missing[1];
      const target = tablesIn(sql).find((t) => tableExists(t) && !columnsOf(t).has(col));
      // Nothing in this query could own the column. That is a genuine mistake in
      // the SQL rather than a stale fixture, and it should read like one.
      if (!target) throw err;
      db.exec(`ALTER TABLE ${target} ADD COLUMN ${col}`);
      DRIFT.add(`${target}.${col}`);
    }
  }
  throw new Error(`could not prepare after 32 shims: ${sql.slice(0, 120)}`);
}

const DB = {
  prepare(sql) {
    return {
      bind(...b) { this._b = b; return this; },
      async all() {
        const st = prepareShimmed(sql);
        return { results: st.all(...(this._b ?? [])) };
      },
      async run() { prepareShimmed(sql).run(...(this._b ?? [])); },
    };
  },
};

// ── R2 stub: the actual published objects ────────────────────────────────────
const VECTORS = {
  // head() mirrors the real R2 binding: search.js version-checks against it
  // before trusting its isolate cache.
  async head(key) {
    const st = statSync(path.join(CF, key));
    return { etag: `${st.size}-${Math.floor(st.mtimeMs)}`, uploaded: new Date(st.mtimeMs) };
  },
  async get(key) {
    const buf = readFileSync(path.join(CF, key));
    return { arrayBuffer: async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength),
             text: async () => buf.toString("utf8") };
  },
};

// ── Workers AI stub: replay a real corpus vector as the "query embedding" ─────
const meta = JSON.parse(readFileSync(path.join(CF, "vectors.json"), "utf8"));
const blob = new Float32Array(readFileSync(path.join(CF, "vectors.f32")).buffer.slice(0));
let PROBE = 0;
const AI = {
  async run(_model, _input) {
    const off = PROBE * meta.dim;
    return { data: [Array.from(blob.slice(off, off + meta.dim))] };
  },
};

// The three vars an instance sets in its own wrangler.toml. The fixture picks
// values that are obviously nobody's, so a test that starts depending on a real
// identity fails here rather than in production.
export const env = {
  DB, VECTORS, AI,
  AUTH_TOKEN: "test-token",
  OWNER_NAME: "Ada",
  OWNER_LABEL: "ada",
  BLOG_URL_TEMPLATE: "https://example.com/posts/{slug}/",
};
export const corpus = { meta, blob, setProbe: (i) => { PROBE = i; } };
