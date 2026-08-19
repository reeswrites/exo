"""T2 — machine-derived zone. Write-scope: derivation engines only.

Every T2 row is stamped author=machine, grounds=False, regenerable=True. The
grounds=False is the wall's other half: even if some future query surfaces a
T2 row, its own envelope declares it may not be read as source.

Derivations read the world through `api.read_source` — the WALL. They see t0 +
t1 and physically cannot see t2, so T2 always rebuilds from the grounding tiers
(the anti-collapse invariant, ADR-0001 Fig 4). `wh verify` proves it.

The one durable derived tier is `atom` (successor spec, Alt B): notes cut into
VERBATIM spans, each tagged with provenance — voice, facet, source. This is the
grounded-generation substrate (your words, cut but never reworded). It is fully
deterministic. There is deliberately NO stored molecule/bond tier: structure —
themes, returns, collisions — is generated ON ASK from atoms + vectors, not
materialized nightly. (Embeddings + the near-graph are the next slice; the
on-ask engine traverses them when asked.)

`affinity` stays as a worked cross-zone example (taste ⋈ notes).
"""
from __future__ import annotations

import hashlib
import re

from . import config, embed, io
from .api import read_source
from .provenance import Row


def emit(zone: str, payload: dict) -> Row:
    """Build a correctly-stamped T2 row. The only sanctioned way to write T2.

    The `warehouse.*` source names here and below are stored identity, not branding:
    they are hashed into every row id already on disk, so renaming one to match the
    system's name would silently re-mint the whole of T2. They stay as they are.
    """
    return Row(
        tier="t2", zone=zone, source="warehouse.derive", author="machine",
        grounds=False, regenerable=True, payload=payload,
    )



def _ready(view: str, *cols: str) -> bool:
    """Does `view` exist with the payload columns a derivation is about to name?

    An empty zone still writes a parquet, but only with the provenance envelope
    — no payload columns at all. A derivation that names one then dies inside
    DuckDB with `Referenced column "title" not found`, which is a true statement
    about the schema and a useless one about the cause: the zone is empty. An
    instance that has not published a blog is not a broken instance, so a
    derivation over an absent input is a skip with a reason, not a crash.
    """
    from . import catalog
    con = catalog.connect("source")
    try:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [view]).fetchall()
    finally:
        con.close()
    have = {r[0] for r in rows}
    return bool(have) and all(c in have for c in cols)


# ────────────────────────────── atomize ──────────────────────────────
# Deterministic. Cut verbatim spans, tag provenance, content-hash identity.
# Keeps second-brain's boundary wisdom (blank-line + authorship-flip, list runs
# stay whole, drop a title-dupe) without its markdown-garden implementation.

_WORD = re.compile(r"[a-z][a-z']+")
_FP = re.compile(r"\b(i|me|my|mine|myself|i'm|i've|i'd|i'll)\b")
_ABOUT_ME_FOLDERS = set(config.setting("vault", "about_me_folders", []))
_FP_FLIP_TO_ME = 0.06
_FP_FLIP_TO_WORLD = 0.01


def _hash(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(b"\x1f")
        h.update(p.encode())
    return h.hexdigest()[:12]


def _cut_blocks(body: str) -> list[str]:
    """Verbatim spans. Boundaries: blank line, or an authorship flip (a
    '>'-quoted line vs an unquoted one) so no atom is half authored words / half a
    pasted quote. A contiguous list run stays one atom (no blank between)."""
    blocks: list[str] = []
    cur: list[str] = []
    cur_quoted: bool | None = None
    for line in body.split("\n"):
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur).strip())
                cur, cur_quoted = [], None
            continue
        q = line.lstrip().startswith(">")
        if cur and q != cur_quoted:
            blocks.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
        cur_quoted = q
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b]


def _quoted_ratio(span: str) -> float:
    lines = [ln for ln in span.split("\n") if ln.strip()]
    if not lines:
        return 0.0
    return sum(1 for ln in lines if ln.lstrip().startswith(">")) / len(lines)


def _voice(span: str) -> str:
    """Attribution by marker, never style inference. A '>'-quoted span is
    something the owner saved (pasted quote / model reply), not their own
    sentence. The unquoted label is the instance's `owner.voice` — it is stored
    in the atom payload and therefore in the atom id, so it is a value an
    instance sets once and never edits."""
    r = _quoted_ratio(span)
    if r == 0.0:
        return config.OWNER_VOICE
    return "quoted" if r >= 0.999 else "mixed"


def _facet(span: str, folder: str) -> str:
    """about-me vs about-world: folder prior + first-person density."""
    low = span.lower()
    words = _WORD.findall(low)
    ratio = (len(_FP.findall(low)) / len(words)) if words else 0.0
    base = "about-me" if folder in _ABOUT_ME_FOLDERS else "about-world"
    if base == "about-world" and ratio >= _FP_FLIP_TO_ME:
        return "about-me"
    if base == "about-me" and ratio <= _FP_FLIP_TO_WORLD:
        return "about-world"
    return base


def _meaningful(span: str, min_words: int = 5) -> bool:
    if not span:
        return False
    lines = [ln for ln in span.split("\n") if ln.strip()]
    if lines and sum(1 for ln in lines if ln.strip().startswith("http")) >= len(lines) / 2:
        return False  # link dump
    return len(_WORD.findall(span.lower())) >= min_words


def atomize() -> list[Row]:
    """t1_notes (raw only) → verbatim atoms, read through the wall.

    Skips refined/ — that's authored prose (output), not raw thinking (input);
    atomizing it would double-count. id = hash(span, source): unchanged span ⇒
    byte-identical atom, so regeneration is idempotent and `wh verify`-clean.
    Exact-duplicate spans across notes collapse to one atom.
    """
    q = (
        "SELECT origin_ref, title, folder, body FROM t1_notes "
        "WHERE note_type <> 'refined' AND body <> '' ORDER BY origin_ref"
    )
    seen: dict[str, dict] = {}
    for ref, title, folder, body in read_source(q):
        title_s = (title or "").strip()
        for span in _cut_blocks(body or ""):
            if span == title_s:            # title-dupe (Apple Notes echoes the title)
                continue
            if not _meaningful(span):
                continue
            key = span.strip()
            if key in seen:                # dedup identical spans across notes
                continue
            seen[key] = {
                "id": _hash(span, ref or ""),
                "note": ref, "title": title,
                "voice": _voice(span), "facet": _facet(span, folder or ""),
                "text": span,
            }
    # origin_ref (envelope) carries the source note — that's what it's for; the
    # deriver name stays in `source`. So an atom is queryable by origin_ref.
    return [
        Row(tier="t2", zone="atom", source="warehouse.atomize", author="machine",
            grounds=False, regenerable=True, id=a["id"], origin_ref=a["note"] or "",
            payload={"title": a["title"], "voice": a["voice"],
                     "facet": a["facet"], "text": a["text"]})
        for a in seen.values()
    ]


# ─────────────────────────── atom_vec (embed) ────────────────────────
def embed_atoms(atom_rows: list[Row]) -> list[Row]:
    """t2_atom_vec: one 384-d vector per atom, keyed by atom id.

    Consumes the atomizer's rows IN MEMORY (never reads the t2_atom view), so the
    wall stays strict. Vectors come from the content-addressed cache, so a re-run
    embeds nothing new and regenerates identically (`wh verify`-clean). This is
    the substrate the on-ask engine traverses — near, clusters, collisions are
    computed from it live, not stored as a tier."""
    # Cap at 2000 chars for embedding only (the full span stays in t2_atom). A
    # few notes have no blank line → one 11k-char atom; uncapped, fastembed pads
    # the whole batch to it and throughput collapses (second-brain's cap, too).
    texts = [r.payload["text"][:2000] for r in atom_rows]
    vecs = embed.embed_texts(texts)
    return [
        Row(tier="t2", zone="atom_vec", source="warehouse.embed", author="machine",
            grounds=False, regenerable=True, id=r.id, payload={"vec": v})
        for r, v in zip(atom_rows, vecs)
    ]


# ─────────────────────────── note_vec (embed) ────────────────────────
def embed_notes() -> list[Row]:
    """t2_note_vec: one 384-d vector per note (raw + refined), keyed by note id.

    Read through the wall (t1_notes) — independent of the atom pass. Body capped at
    2000 chars for embedding (same reason as atoms: fastembed pads a batch to its
    longest text, and one long note would tank throughput). Vectors come from the
    content-addressed cache, so a re-run embeds nothing new and regenerates
    identically (`wh verify`-clean). This is the substrate for note-LEVEL semantic
    search — the on-ask engine and external consumers (Life Terminal's search face)
    embed a query and cosine it against these."""
    rows = read_source(
        "SELECT id, body FROM t1_notes WHERE body <> '' ORDER BY id"
    )
    texts = [(b or "")[:2000] for _id, b in rows]
    vecs = embed.embed_texts(texts)
    return [
        Row(tier="t2", zone="note_vec", source="warehouse.embed", author="machine",
            grounds=False, regenerable=True, id=_id, payload={"vec": v})
        for (_id, _b), v in zip(rows, vecs)
    ]


# ─────────────────────────── post_vec (embed) ────────────────────────
def embed_posts() -> list[Row]:
    """t2_post_vec: one 384-d vector per published blog post, keyed by post id.

    Unlike t1_notes, `t1_post.id` IS unique — it hashes the slug, and Jekyll
    enforces one post per slug — so the join that amplified note vectors into
    1,087,978 rows cannot happen here. The publish-time assertion still runs; a
    guarantee nobody checks is a guarantee for exactly as long as nobody breaks it.

    Embedded text is title + description + the opening of the body. The
    description is the post's own abstract — the sharpest sentence it has about
    itself — so it earns its place ahead of 1,800 chars of prose.
    """
    rows = read_source(
        "SELECT id, title, description, body FROM t1_post ORDER BY id"
    )
    texts = [f"{t}. {d}\n\n{(b or '')[:1800]}" for _id, t, d, b in rows]
    vecs = embed.embed_texts(texts)
    return [
        Row(tier="t2", zone="post_vec", source="warehouse.embed", author="machine",
            grounds=False, regenerable=True, id=_id, payload={"vec": v})
        for (_id, _t, _d, _b), v in zip(rows, vecs)
    ]


# ────────────────────────────── affinity ─────────────────────────────
def affinity() -> list[Row]:
    """artist -> (plays, authored notes mentioning them, blended score).
    Worked cross-zone example. Reads ONLY through the wall (t0_music + t1_notes).
    Word-boundary match (normalize punctuation to spaces); illustrative, not
    production taste scoring."""
    q = r"""
        SELECT m.artist, m.plays, count(DISTINCT n.origin_ref) AS mentions
        FROM (SELECT artist, count(*) AS plays FROM t0_music GROUP BY artist) m
        LEFT JOIN t1_notes n
          ON n.body IS NOT NULL
         AND ' ' || regexp_replace(lower(n.body), '[^a-z0-9]+', ' ', 'g') || ' '
             LIKE '% ' || lower(m.artist) || ' %'
        WHERE m.plays > 100 AND length(m.artist) >= 4
        GROUP BY m.artist, m.plays
    """
    out: list[Row] = []
    for artist, plays, mentions in read_source(q):
        score = int(plays) + int(mentions) * 500
        out.append(emit("affinity", {
            "artist": artist, "plays": int(plays), "mentions": int(mentions), "score": score,
        }))
    out.sort(key=lambda r: (-r.payload["score"], r.payload["artist"]))
    return out


def run_all() -> None:
    atoms = atomize() if _ready("t1_notes", "body", "folder") else []
    if not atoms:
        print("  t2/atom       skipped — t1_notes is empty (nothing to cut)")
    io.write_zone("t2", "atom", atoms)
    print(f"  t2/atom       {len(atoms):>7,} rows  (verbatim · voice/facet/source · deterministic)")
    vecs = embed_atoms(atoms)
    io.write_zone("t2", "atom_vec", vecs)
    print(f"  t2/atom_vec   {len(vecs):>7,} rows  (bge-small 384d · content-addressed cache)")
    nvecs = embed_notes() if _ready("t1_notes", "body") else []
    io.write_zone("t2", "note_vec", nvecs)
    print(f"  t2/note_vec   {len(nvecs):>7,} rows  (bge-small 384d · note-level · cache)")
    pvecs = embed_posts() if _ready("t1_post", "title", "body") else []
    io.write_zone("t2", "post_vec", pvecs)
    print(f"  t2/post_vec   {len(pvecs):>7,} rows  (bge-small 384d · published blog · cache)")
    aff = affinity() if _ready("t0_music", "artist") and _ready("t1_notes", "body") else []
    io.write_zone("t2", "affinity", aff)
    print(f"  t2/affinity   {len(aff):>7,} rows  (cross-zone example)")
