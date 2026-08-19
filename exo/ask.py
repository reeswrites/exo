"""The on-ask engine (seed). Structure is a QUERY, not a stored tier.

Under Alt B nothing bonds atoms nightly. Instead, when you ask — nearest,
themes, collisions — an agent traverses t2_atom + t2_atom_vec live. This module
is the first op: cosine nearest-neighbours. `theme`, `collide`, `returns` land
here later. These are full-profile reads (they may see t2), which is fine — they
DISPLAY derived data, they don't feed it back as source (that's the wall's job,
enforced on derivation, not on asking).
"""
from __future__ import annotations

import numpy as np

from . import embed
from .api import read


def _load_notes():
    """Note vectors joined to their t1_notes envelope, for free-text search."""
    rows = read(
        "SELECT v.id, v.vec, n.title, n.origin_ref, n.note_type, n.body "
        "FROM t2_note_vec v JOIN t1_notes n USING (id)"
    )
    ids = [r[0] for r in rows]
    mat = np.asarray([r[1] for r in rows], dtype=np.float32)
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)  # L2-normalize once
    meta = {r[0]: {"title": r[2], "path": r[3], "note_type": r[4], "body": r[5]} for r in rows}
    return ids, mat, meta


def search_notes(query: str, k: int = 10) -> list[dict]:
    """Top-k notes most similar to a free-text query, by cosine over note vectors.

    Embeds the query ephemerally (no cache write) and ranks the note-vec matrix.
    Returns display-ready hits: id, sim, title, path (origin_ref), note_type, and a
    short snippet. The path points back at the second-brain file, so a consumer can
    open the real note."""
    q = (query or "").strip()
    if not q:
        return []
    ids, mat, meta = _load_notes()
    if not ids:
        return []
    qv = np.asarray(embed.embed_query(q), dtype=np.float32)
    qv /= (np.linalg.norm(qv) + 1e-9)
    sims = mat @ qv
    out = []
    for j in np.argsort(-sims)[:k]:
        m = meta[ids[j]]
        body = (m["body"] or "").strip().replace("\n", " ")
        out.append({
            "id": ids[j], "sim": round(float(sims[j]), 4),
            "title": m["title"], "path": m["path"],
            "note_type": m["note_type"], "snippet": body[:200],
        })
    return out


def _load_chat():
    """Chat turn-chunks + vectors from t0_chat (grounds=false, ADR-0029). Read via the
    full profile (`read`) — the wall hides this zone from derivation, but display sees it."""
    rows = read(
        "SELECT id, vec, channel, origin_ref, origin, title, date, turn, url, text FROM t0_chat"
    )
    ids = [r[0] for r in rows]
    if not ids:
        return [], None, {}
    mat = np.asarray([r[1] for r in rows], dtype=np.float32)
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    meta = {r[0]: {"channel": r[2], "path": r[3], "origin": r[4], "title": r[5],
                   "date": r[6], "turn": r[7], "url": r[8], "text": r[9]} for r in rows}
    return ids, mat, meta


def search_chat(query: str, k: int = 10, channel: str = "both", per_thread: int = 2) -> list[dict]:
    """Top-k chat turn-chunks by cosine over a free-text query. Returns LOCATORS
    (thread path, date, origin, link, snippet) — never draftable prose (ADR-0029:
    world turns point you at where the thinking happened; they don't ground). `channel`
    filters his|world|both; `per_thread` caps hits per source file."""
    q = (query or "").strip()
    if not q:
        return []
    ids, mat, meta = _load_chat()
    if not ids:
        return []
    qv = np.asarray(embed.embed_query(q), dtype=np.float32)
    qv /= (np.linalg.norm(qv) + 1e-9)
    sims = mat @ qv
    out, per = [], {}
    for j in np.argsort(-sims):
        m = meta[ids[j]]
        if channel != "both" and m["channel"] != channel:
            continue
        if per.get(m["path"], 0) >= per_thread:
            continue
        per[m["path"]] = per.get(m["path"], 0) + 1
        out.append({
            "id": ids[j], "sim": round(float(sims[j]), 4), "channel": m["channel"],
            "path": m["path"], "origin": m["origin"], "title": m["title"],
            "date": m["date"], "turn": m["turn"], "url": m["url"],
            "snippet": (m["text"] or "").strip().replace("\n", " ")[:200],
        })
        if len(out) >= k:
            break
    return out


def _load():
    rows = read(
        "SELECT v.id, v.vec, a.text, a.voice, a.facet, a.origin_ref "
        "FROM t2_atom_vec v JOIN t2_atom a USING (id)"
    )
    ids = [r[0] for r in rows]
    mat = np.asarray([r[1] for r in rows], dtype=np.float32)
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)  # L2-normalize once
    meta = {r[0]: {"text": r[2], "voice": r[3], "facet": r[4], "note": r[5]} for r in rows}
    return ids, mat, meta


def nearest(atom_id: str, k: int = 6) -> list[dict]:
    """Top-k atoms most similar to `atom_id` by cosine. Excludes self."""
    ids, mat, meta = _load()
    if atom_id not in meta:
        raise KeyError(f"no atom {atom_id!r} (run `wh rebuild`?)")
    i = ids.index(atom_id)
    sims = mat @ mat[i]
    order = np.argsort(-sims)
    out = []
    for j in order:
        if ids[j] == atom_id:
            continue
        out.append({"id": ids[j], "sim": round(float(sims[j]), 4), **meta[ids[j]]})
        if len(out) >= k:
            break
    return out
