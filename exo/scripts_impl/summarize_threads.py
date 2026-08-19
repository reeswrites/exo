"""`wh summarize-threads` — a distillation of the long conversations.

Poke asked where his head landed on the 80-turn rabbit holes, not just what they
were called. `landed` answers that verbatim, but a closing turn is where a
conversation stopped, which is not always where it arrived.

Deliberate constraints, each from a number rather than a preference:

  only long threads      the median thread is 451 chars, where a 2-3 sentence
                         summary would be longer than the conversation it
                         summarizes; those keep `landed` instead. MIN_CHARS=1000
                         covers 96 threads. Lower than that pays a model to
                         restate three sentences as three sentences.
  build time, cached     content-addressed on the input, so an unchanged thread
                         never regenerates and `wh verify` stays deterministic.
                         Generating on read would put a model call in the latency
                         path of every search and re-bill the same thread forever.
  his turns only         the assistant's side is not summarized and not stored.
  attributed             the model is recorded on every row. This is machine
                         prose about him, the one thing the vault's authorship
                         rule forbids inbound — permitted here because it goes
                         OUT, is never written to the vault, is grounds=false,
                         and ships beside the verbatim it derives from.
  never embedded         summaries stay out of every vector space, so search
                         keeps returning his words rather than a paraphrase.

Absent a token this is a no-op: existing summaries stand, nothing fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request

import pyarrow as pa
import pyarrow.parquet as pq

from .. import config

MIN_CHARS = 1000
# Bump when the prompt or model changes, or the cache serves output from an
# instruction that no longer exists — the key hashes the thread, not the ask.
PROMPT_VERSION = "v4-open"
MODEL = "@cf/meta/llama-3.1-8b-instruct"
CACHE = config.CACHE / "thread_summary.parquet"
MAX_INPUT = 12000   # bge-unrelated; keeps one prompt bounded for the 17k outlier

_SYSTEM = (
    "Summarize ONE PERSON'S side of a conversation. You see only what they typed; "
    "the assistant's replies are not shown and must not be inferred.\n\n"
    "Rules:\n"
    "- At most 3 sentences. No headings, no bold, no bullets, no markdown.\n"
    "- Use 'they/them'. Never guess gender, and never use a name.\n"
    "- Past tense: these are finished conversations, not live ones. Never write "
    "'is currently thinking'.\n"
    "- Say where the thinking LANDED: the position reached, the decision made, or "
    "the constraint settled on.\n"
    "- When told the conversation ENDED OPEN, you must open with 'No conclusion "
    "reached' and describe what they were working out. Do not write 'they "
    "concluded' in that case. This is common and is the correct answer.\n"
    "- Only write 'they concluded' or 'they decided' when the text shows them "
    "settling something. A question they asked is not a conclusion they reached.\n"
    "- Never invent a resolution that is not in the text.\n"
    "- Third person only. Never 'you' or 'we' — the reader is not the person "
    "being summarized.\n"
    "- Describe what THEY concluded, not what is true. 'They decided X', not "
    "'X is the case'. You are reporting a position, not asserting it.\n"
    "- Start with the substance. No preamble, no restating the topic."
)


def _load_cache() -> dict[str, dict]:
    """Current-version rows only, one per thread.

    An unreadable cache is an empty cache, not a crash — a failed download leaves
    a zero-byte file, and treating that as fatal turns a missing optional input
    into a broken nightly.

    Rows from superseded prompts are dropped here rather than kept. They are dead
    weight, and worse: publish LEFT JOINs this on `title`, so a title carrying two
    summaries emitted the topic twice. One prompt bump turned 52 topics into 104
    rows — the same non-unique-join failure as t1_notes.id, one table over.
    """
    if not CACHE.exists() or CACHE.stat().st_size == 0:
        return {}
    try:
        t = pq.read_table(CACHE).to_pylist()
    except Exception as exc:
        print(f"  cache unreadable ({str(exc)[:60]}) — regenerating from scratch")
        return {}
    keep = {r["key"]: r for r in t if r.get("version") == PROMPT_VERSION}
    if len(keep) < len(t):
        print(f"  dropped {len(t) - len(keep)} row(s) from superseded prompts")
    return keep


def _generate(account: str, token: str, text: str, ended_open: bool = False) -> str | None:
    # Told, not inferred. Asked to judge for itself whether a thread concluded,
    # the model said "they concluded" for 96 of 96 — including 21 that end on a
    # question from him, where it turned his asking into his deciding. Whether
    # the last thing he wrote is a question is a fact about the text, so it is
    # computed here and stated, rather than left to the summarizer to notice.
    prefix = (
        "This conversation ENDED OPEN: the last thing they wrote was a question "
        "or a request, not a conclusion.\n\n" if ended_open else ""
    )
    body = json.dumps({
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prefix + text[:MAX_INPUT]},
        ],
        "max_tokens": 160,
    }).encode()
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            out = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"    generation failed: {exc}")
        return None
    if not out.get("success"):
        print(f"    api error: {str(out.get('errors'))[:120]}")
        return None
    return " ".join((out.get("result", {}).get("response") or "").split()) or None


def run() -> int:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()

    # Read the vault, not the catalog. t0_chat is written by `wh chatimport`,
    # which only the laptop runs — CI checks the vault out but never builds that
    # zone, so this died on "Table with name t0_chat does not exist". The chat
    # topics loader reads the same source, so this now works anywhere the vault
    # is present, which is the only real precondition.
    from ..loaders import chat as chat_loader

    by_title: dict[str, list[tuple[int, str]]] = {}
    for u in chat_loader.build_units():
        if u.get("channel") != "his":
            continue
        title = (u.get("title") or "").strip()
        if not title:
            continue
        by_title.setdefault(title, []).append((int(u.get("turn") or 0), u.get("text") or ""))

    threads = []
    for title, turns in by_title.items():
        ordered = sorted(turns)
        body = "\n".join(t for _, t in ordered)
        if len(body) < MIN_CHARS:
            continue
        # Their last substantive line. A trailing "?" means they were still
        # asking when it stopped.
        tail = next((t for _, t in reversed(ordered) if len(t.strip()) > 40), "")
        threads.append((title, body, tail.rstrip().endswith("?")))

    cache = _load_cache()
    todo = []
    for title, body, ended_open in threads:
        # Key on the CONTENT: a thread that grows gets a new key and is
        # resummarized; one that is merely re-ingested is not.
        key = hashlib.sha256(
            (PROMPT_VERSION + "\x1f" + title + "\x1f" + (body or "")).encode()
        ).hexdigest()[:16]
        if key not in cache:
            todo.append((key, title, body, ended_open))

    print(f"summarize-threads: {len(threads)} thread(s) over {MIN_CHARS:,} chars · "
          f"{len(cache)} cached · {len(todo)} to generate")

    if todo and not (account and token):
        print("  no CLOUDFLARE_ACCOUNT_ID/API_TOKEN — leaving existing summaries as they are")
        return 0

    made = 0
    for key, title, body, ended_open in todo:
        text = _generate(account, token, body or "", ended_open)
        if not text:
            continue
        cache[key] = {"key": key, "title": title, "summary": text,
                      "model": MODEL, "version": PROMPT_VERSION}
        made += 1
        print(f"  + {title[:52]}")

    if made:
        config.ensure_dirs()
        pq.write_table(pa.Table.from_pylist(list(cache.values())), CACHE)
        print(f"  wrote {len(cache)} summaries -> {CACHE.name}")
    return 0
