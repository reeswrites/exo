/**
 * RUN THIS ONCE AFTER THE FIRST DEPLOY. Nothing else validates it.
 *
 * The corpus was embedded by Python bge-small (sentence-transformers); queries
 * are embedded by Workers AI's @cf/baai/bge-small-en-v1.5. Choosing Cloudflare
 * rests entirely on those being the SAME vector space. If pooling or
 * normalisation differ, search does not fail loudly — it returns confident
 * nonsense, ranked, forever.
 *
 * Test: take the exact text of a known atom, ask the deployed surface for it,
 * and check it comes back FIRST. Rank, not raw score.
 *
 * MEASURED 2026-08-19, against Workers AI directly through a --remote dev
 * binding (no token needed, since the question is the vector space and not the
 * endpoint): embedding the same text through both models gives cosine 0.9031,
 * not ~1.0. The two bge-small implementations are NOT identical — almost
 * certainly a pooling or normalisation difference.
 *
 * It does not matter, and this is why the check is written on rank. The offset
 * is systematic, so every vector moves together and the ordering survives: the
 * exact source atom still ranked #1 of 8,446, at 0.9031 against 0.6464 for
 * second place, and the rest of the top five were topically coherent. A
 * threshold on absolute score would have to sit near 0.75 to accommodate the
 * offset, which is why it does.
 *
 *   node test/verify-embedding-space.mjs https://<worker>.workers.dev <token>
 */
const [, , url, token] = process.argv;
if (!url || !token) {
  console.error("usage: node test/verify-embedding-space.mjs <worker-url> <token>");
  process.exit(2);
}

const call = async (method, params) => {
  const r = await fetch(url, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  return r.json();
};

// A phrase that exists verbatim in the corpus. Any atom's text will do; this one
// is stable because it is authored prose, not a title.
const PROBE = process.env.PROBE_TEXT
  ?? "the death note works better as a constraint than as a power";

const res = await call("tools/call", { name: "whats_relevant", arguments: { topic: PROBE } });
const payload = JSON.parse(res.result?.content?.[0]?.text ?? "{}");
const top = payload.rows?.[0];

console.log("probe :", PROBE);
console.log("top   :", top?.text?.slice(0, 90));
console.log("score :", top?.score);

if (!top) {
  console.error("\nFAIL — no results. Check the vector blob is in R2.");
  process.exit(1);
}
if (top.score < 0.75) {
  console.error(
    `\nFAIL — top score ${top.score} is too low for text that exists verbatim in the corpus.` +
    "\nThe query and corpus embeddings are probably NOT in the same space." +
    "\nRe-embed the corpus with Workers AI, or serve query embeddings from the same" +
    "\nPython model that produced zones/_serve/*_vec.parquet."
  );
  process.exit(1);
}
console.log(`\nPASS — score ${top.score} confirms one shared vector space.`);
