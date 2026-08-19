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

const DB = {
  prepare(sql) {
    return {
      bind(...b) { this._b = b; return this; },
      async all() {
        const st = db.prepare(sql);
        return { results: st.all(...(this._b ?? [])) };
      },
      async run() { db.prepare(sql).run(...(this._b ?? [])); },
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
