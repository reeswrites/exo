"""Chat-logs → the grounds=false `t0_chat` zone (ADR-0029).

second-brain's chat-logs are a SEPARATE provenance class: grounds=false, two
channels — `his` (the owner's own turns, marked with their handle) and
`world` (machine turns from
services that never saw the vault: claude.ai, google-ai-mode, chatgpt.com). By
ADR-0029 they must never merge with the grounding corpus and must never feed
derivation.

The channel value `his` is STORED IDENTITY, not a description of anyone: row ids
hash it, and the read surface publishes a `his_turns` column built from it. Read
it as a label for "the owner's side of the conversation" and leave it alone —
renaming it to read better re-mints every id built from it (ADR-0014 §7).

Home is a real tier zone (`t0_chat`), not the cache: chat is pre-promotion INPUT
— owner turns graduate into `t1_notes` (raw) via promotion, world turns are
external — so it sits at the t0 input layer, grounds=false, per-row author (human
for owner turns, external for world). The GROUNDS-AWARE WALL keeps it out of
derivation: `read_source` filters `WHERE grounds`, so a t2 derivation physically
cannot read t0_chat. That is exactly why the chat vectors are embedded HERE by the
indexer (display-side) and stored inline, NOT produced by a walled t2 derivation —
a derivation couldn't see grounds=false chat to embed it. `regenerable=false`: it
is a projection of an external record (the chat-logs), not output derived from a
lower tier — same posture as `t1_notes`. Source of truth stays
second-brain/data/chat-logs until retirement; `wh chatindex` rebuilds this zone.

The turn/chunk/channel rules mirror second-brain's `cli/search/chat.py` and
`cli/capture/normalize.py` (kept in sync deliberately — the judgment is a small,
stable marker split). Machine turns from a VAULT-AWARE origin (claude-code) are
NOT indexed (the ADR-0013 ouroboros); iMessage threads are skipped (ADR-0021).

    wh chatindex     # rebuild t0_chat from second-brain chat-logs
"""
from __future__ import annotations

import re

import yaml

from .. import config, embed, io
from ..provenance import Row, stable_id

# ── mirrors of cli/search/chat.py + cli/capture/normalize.py (keep in sync) ──
# The owner's handle is how their own turns are spelled in an export, so it
# is an instance fact. `channel` — what actually reaches the store — stays
# `his`/`world`, so a handle change never re-mints an id.
_HANDLE = re.escape(config.OWNER_VOICE)
_TURN = re.compile(rf"^\*\*({_HANDLE}|assistant|shell|system)\*\* · (\S+)\s*$", re.M)
_SRC_LINK = re.compile(r"^\s*\d+\.\s*\[([^\]]+)\]\((\S+)\)\s*$", re.M)
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
ORIGIN_CLAUDE_CODE = "claude-code"
ORIGIN_IMESSAGE = "imessage"
VAULT_AWARE_ORIGINS = frozenset({ORIGIN_CLAUDE_CODE})


def _vault_aware(origin: str) -> bool:
    return origin in VAULT_AWARE_ORIGINS


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        fm = yaml.safe_load(text[3:end]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, text[end + 4:].lstrip("\n")


def _chunks(text: str) -> list[str]:
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        cut = text.rfind("\n", i + CHUNK_CHARS // 2, i + CHUNK_CHARS)
        if cut <= i:
            cut = min(i + CHUNK_CHARS, len(text))
        out.append(text[i:cut].strip())
        i = max(cut - CHUNK_OVERLAP, cut) if cut < len(text) else cut
    return [c for c in out if c]


def _turns(body: str) -> list[tuple[str, str, str]]:
    """(speaker, stamp, text) per turn — same split as chat.py so the two agree."""
    parts = _TURN.split(body)
    return [(parts[i], parts[i + 1], parts[i + 2]) for i in range(1, len(parts) - 2, 3)]


def build_units() -> list[dict]:
    """One dict per turn-chunk from second-brain chat-logs, channel-tagged."""
    base = config.VAULT
    chat_dir = base / "chat-logs"
    units: list[dict] = []
    for p in io.markdown(chat_dir):
        if p.name == "promote-queue.md":
            continue
        fm, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        if fm.get("type") != "chat":
            continue
        origin = fm.get("origin") or (
            ORIGIN_IMESSAGE if fm.get("via") == ORIGIN_IMESSAGE else ORIGIN_CLAUDE_CODE)
        if origin == ORIGIN_IMESSAGE:   # a text thread is captured for attribution, not retrieval
            continue
        aware = _vault_aware(origin)
        rel = str(p.relative_to(base))
        for n, (speaker, stamp, text) in enumerate(_turns(body), 1):
            if speaker == config.OWNER_VOICE:
                channel = "his"
            elif speaker == "assistant":
                if aware:               # vault-aware machine turn — the ouroboros, skip
                    continue
                channel = "world"
            else:                       # shell / system — not a retrieval unit
                continue
            for c in _chunks(text):
                units.append({
                    "channel": channel, "path": rel,
                    "session": str(fm.get("session", "") or ""),
                    "origin": origin, "title": str(fm.get("title", p.stem)),
                    "date": str(stamp or fm.get("created", ""))[:10],
                    "url": str(fm.get("thread_url", "") or ""), "turn": n, "text": c,
                })
    return units


def run() -> None:
    units = build_units()
    vecs = embed.embed_texts([u["text"][:CHUNK_CHARS] for u in units])
    rows = []
    for u, v in zip(units, vecs):
        rid = stable_id("second-brain.chat-logs", u["path"], str(u["turn"]), u["text"])
        rows.append(Row(
            tier="t0", zone="chat", source="second-brain.chat-logs",
            author="human" if u["channel"] == "his" else "external",
            grounds=False, regenerable=False, created=u["date"] or None,
            origin_ref=u["path"], id=rid,
            payload={"channel": u["channel"], "origin": u["origin"], "title": u["title"],
                     "session": u["session"], "turn": u["turn"], "url": u["url"],
                     "date": u["date"], "text": u["text"], "vec": v},
        ))
    io.write_zone("t0", "chat", rows)
    his = sum(1 for u in units if u["channel"] == "his")
    print(f"  t0/chat       {len(rows):>7,} rows  ({his} his / {len(units) - his} world · "
          f"bge-small 384d · grounds=false → wall-invisible, per-row author)")


def _landed(his_texts: list[tuple[int, str]], limit: int = 420) -> str:
    """The last substantial owner turn — the closest thing to a written conclusion."""
    if not his_texts:
        return ""
    last = max(his_texts, key=lambda t: t[0])[1]
    return last if len(last) <= limit else last[:limit].rsplit(" ", 1)[0] + "\u2026"


def _digest(title: str, his_texts: list[tuple[int, str]], limit: int = 1400) -> str:
    """Title + a spread of owner turns, for embedding. bge-small truncates around
    512 tokens, so this samples across the thread rather than taking the head:
    a long conversation's substance is rarely in its opening."""
    if not his_texts:
        return title
    ordered = [t for _, t in sorted(his_texts)]
    picks, step = [], max(1, len(ordered) // 6)
    for i in range(0, len(ordered), step):
        picks.append(ordered[i])
        if sum(len(x) for x in picks) > limit:
            break
    return (title + ". " + " ".join(picks))[:limit]


def topics() -> list[Row]:
    """One row per conversation: title, when, how much — never the turns.

    Open threads record what was written down; this records what was worked
    through out loud, which is a different and fresher signal. The vault lags a
    deliberate act of capture; a conversation happens whether or not it is filed.

    Titles and volume only. `t0_chat` itself stays held (ADR-0005) — 10,019 turns
    of unbounded content is the wrong thing to put behind a token, and the topic
    is what an assistant actually needs. Turn counts carry what a title cannot:
    forty turns on one question is a preoccupation, three is a passing look.

    A loader rather than a T2 derivation, deliberately. Chat is grounds=false, so
    the source profile hides its rows from derivation entirely — the wall exists
    precisely so conversation cannot feed the self-model (ADR-0016). Emitting
    grounds=false keeps that true of this zone too: nothing may derive from it.
    Titles are auto-generated by the assistant that held the conversation, so
    author=machine; they are labels, not authored prose.
    """
    by_title: dict[str, dict] = {}
    for u in build_units():
        title = (u.get("title") or "").strip()
        if not title:
            continue
        d = str(u.get("date") or "")[:10]   # build_units emits `date`, not `created`
        rec = by_title.setdefault(title, {"turns": 0, "his": 0, "first": d, "last": d,
                                          "his_texts": []})
        rec["turns"] += 1
        if u.get("channel") == "his":
            rec["his"] += 1
            txt = " ".join((u.get("text") or "").split())
            if len(txt) > 60:
                rec["his_texts"].append((int(u.get("turn") or 0), txt))
        if d:
            rec["first"] = min(rec["first"] or d, d)
            rec["last"] = max(rec["last"] or d, d)

    # Embedded here for the same reason the turns are (see module docstring):
    # chat is grounds=false, so no derivation can see it to embed it. Only the
    # digest is embedded — the owner's words. The generated summary, when one exists,
    # never enters a vector space.
    titles = list(by_title)
    digests = [_digest(t, by_title[t]["his_texts"]) for t in titles]
    vecs = embed.embed_texts(digests) if digests else []
    vec_by_title = dict(zip(titles, vecs))

    rows: list[Row] = []
    for title, r in by_title.items():
        rows.append(Row(
            tier="t0", zone="chat_topic", source="second-brain/chat-logs",
            author="machine", grounds=False,
            created=r["last"] or None,
            origin_ref="chat-logs",
            id=stable_id("chat_topic", title),
            payload={
                "title": title,
                "started": r["first"],
                "last_seen": r["last"],
                "turns": r["turns"],
                "his_turns": r["his"],
                # Where it landed, in the owner's words. Extraction, not generation:
                # free, deterministic, and a reader can check a summary against
                # it rather than trusting one.
                "landed": _landed(r["his_texts"]),
                # The retrieval key. Title plus a bounded sample of owner turns —
                # embedding the closing turn ALONE would make a 300-turn thread
                # unfindable whenever it happens to end in "yeah maybe". Still
                # only authored words: no machine text enters any vector space.
                "digest": _digest(title, r["his_texts"]),
                "vec": vec_by_title.get(title),
            },
        ))
    rows.sort(key=lambda x: (x.payload["last_seen"], x.payload["title"]), reverse=True)
    return rows
