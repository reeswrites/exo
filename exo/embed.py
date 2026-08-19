"""Embeddings — a content-addressed vector cache over text. Offline.

Vectors are bound to text BYTES, not to atoms: the cache keys on
`sha256(text)[:16]`, so any caller that produces the same text reuses the same
vector for free (and second-brain's cache is the same model, so the two can
converge later). A re-run only embeds genuinely new text.

Model: BAAI/bge-small-en-v1.5 via fastembed (ONNX, fully offline after the
one-time model download). 384-dim, float32, rounded to 6dp so cached vectors —
and therefore `wh verify` digests — are byte-stable.
"""
from __future__ import annotations

import hashlib

import pyarrow as pa
import pyarrow.parquet as pq

from . import config

MODEL = "BAAI/bge-small-en-v1.5"
DIMS = 384
VEC_PRECISION = 6
# fastembed's default batch pads every text in a batch to the longest one, so a
# big batch with one long span is catastrophically slow. 32 is second-brain's
# measured sweet spot (larger was 11-18x slower). Text is also capped upstream.
BATCH = 32

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL)
    return _model


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_cache() -> dict[str, list[float]]:
    if not config.EMBED_CACHE.exists():
        return {}
    t = pq.read_table(config.EMBED_CACHE)
    return dict(zip(t.column("key").to_pylist(), t.column("vec").to_pylist()))


def _save_cache(cache: dict[str, list[float]]) -> None:
    config.ensure_dirs()
    t = pa.table({"key": list(cache.keys()), "vec": list(cache.values())})
    pq.write_table(t, config.EMBED_CACHE)


def embed_query(text: str) -> list[float]:
    """Embed one ephemeral string (a search query) WITHOUT touching the
    content-addressed cache. Queries aren't corpus text — caching them would
    bloat the cache with strings that never recur. Same model, same rounding, so
    a query vector is directly comparable to cached corpus vectors."""
    model = _get_model()
    vec = next(iter(model.embed([text], batch_size=1, parallel=0)))
    return [round(float(x), VEC_PRECISION) for x in vec]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Vectors for `texts`, in order. Embeds only cache misses, then persists."""
    cache = _load_cache()
    misses = [t for t in dict.fromkeys(texts) if _key(t) not in cache]  # unique, order-preserving
    if misses:
        model = _get_model()
        # batch_size caps padding waste; parallel=0 fans the encode across all
        # cores (fastembed's recommendation for large sets) — ~1 core does 11/s.
        for text, vec in zip(misses, model.embed(misses, batch_size=BATCH, parallel=0)):
            cache[_key(text)] = [round(float(x), VEC_PRECISION) for x in vec]
        _save_cache(cache)
    return [cache[_key(t)] for t in texts]
