/**
 * Vector search, without a vector database.
 *
 * The whole corpus is 10,971 x 384d unit-norm float32 = 16.85MB, which fits in a
 * Worker isolate with 111MB to spare. Brute-force dot product over that is ~4M
 * multiply-adds — sub-millisecond, and far simpler than keeping a second store
 * in sync. Vectorize was rejected deliberately: its free tier is ~5M stored
 * dimensions and this corpus already sits at 4.21M, so it would be outgrown
 * within months of ordinary note-writing (ADR-0005).
 *
 * Vectors are unit-norm at publish time, so cosine similarity IS the dot
 * product. Only the query embedding needs normalising.
 */

// Per-isolate, one entry per index ("vectors" = the owner's writing, "saves" =
// their bookmarks); survives across requests, rebuilt on cold start.
const CACHES = new Map();

export async function loadIndex(env, name = "vectors") {
  // Version-check before trusting the cache. An isolate can outlive a nightly
  // publish, and the failure is silent: it keeps the old vectors while D1 holds
  // the new rows, so hits resolve to refs that no longer exist — or worse, to a
  // different note that now owns that hash. One HEAD per search is cheap next to
  // a 14MB load, and it turns "intermittently wrong" into "correct".
  const head = await env.VECTORS.head(`${name}.f32`);
  if (!head && name !== "vectors") return null; // an optional index not yet published
  const version = head?.etag ?? head?.uploaded?.toISOString?.() ?? null;
  const hit = CACHES.get(name);
  if (hit && hit.version === version) return hit;

  const [blobObj, metaObj] = await Promise.all([
    env.VECTORS.get(`${name}.f32`),
    env.VECTORS.get(`${name}.json`),
  ]);
  if (!blobObj || !metaObj) throw new Error(`vector index ${name} missing from R2`);

  const meta = JSON.parse(await metaObj.text());
  const buf = await blobObj.arrayBuffer();
  const vectors = new Float32Array(buf);

  const expected = meta.count * meta.dim;
  if (vectors.length !== expected) {
    // A truncated or misaligned blob silently returns plausible nonsense, which
    // is worse than an error — every hit would be confidently wrong.
    throw new Error(`vector blob length ${vectors.length} != ${expected}`);
  }

  const entry = { vectors, rows: meta.rows, dim: meta.dim, count: meta.count, version };
  CACHES.set(name, entry);
  return entry;
}

/** Embed a query in the same space as the corpus, then unit-normalise it. */
export async function embed(env, text) {
  const res = await env.AI.run("@cf/baai/bge-small-en-v1.5", { text: [text] });
  const raw = res?.data?.[0];
  if (!Array.isArray(raw)) throw new Error("embedding failed");

  let norm = 0;
  for (const x of raw) norm += x * x;
  norm = Math.sqrt(norm) || 1;
  const out = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw[i] / norm;
  return out;
}

/**
 * Top-k by dot product. `kind` filters to 'atom' or 'note'; omit for both.
 * Keeps a small running top-k rather than sorting 10,971 scores.
 */
export async function search(env, query, { k = 10, kind = null, index = "vectors" } = {}) {
  const idx = await loadIndex(env, index);
  if (!idx) return null; // that index is not published; the caller falls back
  const q = await embed(env, query);
  if (q.length !== idx.dim) throw new Error(`query dim ${q.length} != ${idx.dim}`);

  const { vectors, rows, dim, count } = idx;
  const best = []; // ascending by score, length <= k

  for (let i = 0; i < count; i++) {
    if (kind && rows[i].kind !== kind) continue;

    let dot = 0;
    const off = i * dim;
    for (let j = 0; j < dim; j++) dot += vectors[off + j] * q[j];

    if (best.length < k) {
      best.push({ i, score: dot });
      best.sort((a, b) => a.score - b.score);
    } else if (dot > best[0].score) {
      best[0] = { i, score: dot };
      best.sort((a, b) => a.score - b.score);
    }
  }

  return best
    .reverse()
    .map(({ i, score }) => ({ ...rows[i], score: Math.round(score * 1000) / 1000 }));
}
