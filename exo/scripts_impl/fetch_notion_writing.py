"""`exo fetch-notion-writing` — the owner's Notion writing, cached for indexing.

Notion is the source of truth for the owner's writing; Exo's copy is an index
that exists to be searched by meaning (`notes_on`, `whats_relevant`), which
Notion's keyword search cannot do. This command READS everything the integration
can see into `raw/notion-writing/pages.json` and writes two reports beside it.
It changes no loader and no zone.

**Scope is the integration's connection, not this code.** The owner keeps one
root page (Home) and connects a read-only integration to it alone; `/search`
then returns every page and database row beneath it, with `last_edited_time`,
at one request per hundred objects. That one listing is discovery, the
incremental watermark and deletion detection at once. A database listed in
`[notion_writing] skip_databases` keeps its rows' properties but is never read
for bodies (the Reading Log's 994 bare book rows cost a request each to learn
they have no prose).

**What gets indexed** is decided downstream from `body_chars` against
`min_body_chars` (default 200): bare menu and tracker rows fall out without a
list of them having to be kept.

**Adoption.** ~1,900 pages are the Apple Notes archive, which the vault already
holds as `raw/import/*.md` under `source = "second-brain"`. Atoms hash
`origin_ref`, so those pages must be adopted into the rows they already are, not
minted again. The two systems share no id (Notion's "Apple Notes ID" is the Core
Data URI the importer read; the vault's `uuid` is the ZIDENTIFIER from another
exporter), so `match-report.json` pairs them on (normalised title, created ±1
day), one-to-one or not at all, and records the page -> origin_ref map.

**Authorship.** `notes_on` answers "what has he thought", so prose Claude drafted
must not come back in his voice. Each page carries `authorship`:

  human       no marker, no decision, no suspicion
  ai | mixed  from, in order: the owner's decision in `notion-authorship.json`
              (instance root, the same `{author, decided, why}` shape as the
              vault's authorship.json), an `Authorship` select property, or a
              marker line at the top of the body ("Drafted with Claude", see
              MARKER)
  unreviewed  no decision, but the heuristic in `suspicion()` fired. Listed in
              `authorship-review.json` for the owner to decide. Never silently
              promoted to `ai`: a wrong label hides his own words.

`created_by` / `last_edited_by` are recorded (person vs bot) because pages made
through an internal integration token are attributed to the bot, while pages made
through the hosted Notion connector are attributed to the person — so they
separate some AI pages and not others, and the report counts both.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

from .. import config
from ..notes.sources.notion import api
from ..notes.sources.notion import blocks as blockrender

MAX_DROP = 0.10          # a tenth of the record gone in one listing is a fault, not an edit
MIN_BODY_CHARS = 200     # below this a page is a row, not writing

# The marker a Claude-drafted page carries as its first line, in a callout or
# not. "Drafted" -> ai; "edited"/"co-written"/"revised" -> mixed.
MARKER = re.compile(
    r"^(?:>\s*)?(?:\S{1,2}\s+)?(?P<verb>drafted|written|generated|edited|co-written|revised)"
    r"\s+(?:with|by)\s+(?:claude|ai|an ai)\b", re.IGNORECASE)
_MIXED_VERBS = {"edited", "co-written", "revised"}
AUTHORSHIP_PROP = {"mine": "human", "his": "human", "human": "human",
                   "ai-drafted": "ai", "ai": "ai", "claude": "ai",
                   "mixed": "mixed"}


def cache_dir() -> Path:
    return config.ROOT / "raw" / "notion-writing"


def _cfg(key: str, default):
    return config.setting("notion_writing", key, default)


def _ids(env: str, key: str) -> set[str]:
    raw = os.environ.get(env, "").strip()
    vals = raw.split(",") if raw else (_cfg(key, []) or [])
    return {api._norm_id(v.strip()) for v in vals if v.strip()}


# ─────────────────────────────── one page ───────────────────────────────


def _prop(page: dict, name: str) -> dict:
    return (page.get("properties") or {}).get(name) or {}


def _date(page: dict, name: str) -> str:
    return ((_prop(page, name).get("date") or {}).get("start") or "").strip()


def _check(page: dict, name: str) -> bool:
    return bool(_prop(page, name).get("checkbox"))


_USERS: dict[str, str] = {}


def _load_users() -> None:
    """id -> "person" | "bot", once per run. A page's created_by carries only an
    id; the type needs the user list, which needs the integration's "read user
    information" capability. Without it every author reads "unknown"."""
    _USERS.clear()
    try:
        for u in api._paged("/users"):
            _USERS[api._norm_id(u.get("id", ""))] = u.get("type", "")
    except api.NotionError as exc:
        # Expected for a read-content-only integration; not an error.
        print("  users: no 'Read user information' capability — created_by types read 'unknown'")


def _who(obj: dict) -> dict:
    u = obj or {}
    uid = api._norm_id(u.get("id", ""))
    return {"type": u.get("type") or _USERS.get(uid) or "unknown", "id": uid}


def shape(page: dict, index: dict) -> dict:
    """The properties this index keeps, without the body."""
    pid = api._norm_id(page.get("id", ""))
    parent = page.get("parent") or {}
    folder = (_prop(page, "Folder").get("select") or {}).get("name", "")
    return {
        "page_id": pid,
        "title": api.title_of(page).strip(),
        "path": api._folder_of(pid, index),
        "database": api._norm_id(parent.get("database_id", "")),
        # The Apple Notes folder where the page has one (the archive's serve/hold
        # axis is these names); otherwise its place in the Home tree is `path`.
        "folder": folder,
        # The Apple date when the page carries one; created_time is the import
        # date for the whole archive (2026-08-20).
        "created": _date(page, "Created") or page.get("created_time", ""),
        "created_time": page.get("created_time", ""),
        "edited": page.get("last_edited_time", ""),
        "created_by": _who(page.get("created_by")),
        "last_edited_by": _who(page.get("last_edited_by")),
        "apple_notes_id": api._prop_text(_prop(page, "Apple Notes ID")).strip(),
        "authorship_prop": api._prop_text(_prop(page, "Authorship")).strip(),
        "locked": _check(page, "Locked"),
        "skip": _check(page, "Skip?"),
        "url": page.get("url", ""),
    }


# ─────────────────────────────── authorship ───────────────────────────────

_EMDASH = re.compile("—")
_HEADING = re.compile(r"^#{1,3} ", re.MULTILINE)
_CLOSING_OFFER = re.compile(
    r"(want me to|would you like me to|let me know if|i can also|happy to)\b[^\n]{0,120}\??\s*$",
    re.IGNORECASE)
_NOT_X_BUT_Y = re.compile(r"\b(?:it'?s|this is|that'?s) not (?:just )?[^.,;]{1,40}[,;—-]+ (?:it'?s|but)\b",
                          re.IGNORECASE)


def marker(body: str) -> str | None:
    for line in (body or "").splitlines()[:3]:
        line = line.strip()
        if not line:
            continue
        m = MARKER.match(line)
        if m:
            return "mixed" if m.group("verb").lower() in _MIXED_VERBS else "ai"
    return None


def suspicion(entry: dict) -> list[str]:
    """Signals that a page may be Claude's prose. For the review list only."""
    body = entry.get("body") or ""
    n = max(len(body), 1)
    sig = []
    # An integration as creator is only a signal outside the Apple Notes archive:
    # every archive page was created by the importer bot, and every one is his.
    # Without "Read user information" the type is "unknown" and this never fires.
    if entry["created_by"]["type"] == "bot" and not entry.get("apple_notes_id"):
        sig.append("created by an integration")
    if len(_EMDASH.findall(body)) * 1000 / n >= 3:
        sig.append("em-dash density")
    if len(_HEADING.findall(body)) >= 3 and n >= 1500:
        sig.append("sectioned with headings")
    if _CLOSING_OFFER.search(body.rstrip()[-300:]):
        sig.append("ends on an offer")
    if len(_NOT_X_BUT_Y.findall(body)) >= 2:
        sig.append("'not X, it's Y' construction")
    if "research log" in (entry.get("path") or "").lower():
        sig.append("under Research Log (skill-written)")
    return sig


def decisions() -> dict:
    p = config.ROOT / "notion-authorship.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def authorship(entry: dict, decided: dict) -> str:
    d = decided.get(entry["page_id"])
    if isinstance(d, dict) and d.get("author") in ("human", "ai", "mixed"):
        return d["author"]
    prop = AUTHORSHIP_PROP.get(entry.get("authorship_prop", "").lower())
    if prop:
        return prop
    m = marker(entry.get("body", ""))
    if m:
        return m
    return "unreviewed" if len(suspicion(entry)) >= 2 else "human"


# ─────────────────────────────── the cache ───────────────────────────────


def _cache_path() -> Path:
    return cache_dir() / "pages.json"


def read_cache() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def sync(*, full: bool = False, bodies: bool = True) -> dict:
    """List everything, fetch changed bodies, return the new cache blob."""
    old = {} if full else read_cache().get("pages", {})
    everything = list(api._paged("/search", method="POST", body={
        "sort": {"direction": "descending", "timestamp": "last_edited_time"}}))
    index = api._index(everything)
    _load_users()
    pages = [o for o in everything if o.get("object") == "page"
             and not (o.get("archived") or o.get("in_trash"))]
    if not pages:
        raise RuntimeError("search returned no pages — refusing to write "
                           "(is the integration connected to the root page?)")
    if old and len(pages) < int(len(old) * (1 - MAX_DROP)):
        raise RuntimeError(f"{len(pages):,} pages, but the cache held {len(old):,} — more "
                           f"than {MAX_DROP:.0%} gone in one listing; refusing to write")

    skip = _ids("EXO_NOTION_WRITING_SKIP", "skip_databases")
    decided = decisions()
    out: dict[str, dict] = {}
    fetched = reused = skipped_rows = 0
    dropped_kinds: dict[str, int] = {}
    for page in pages:
        entry = shape(page, index)
        prev = old.get(entry["page_id"])
        if entry["database"] in skip:
            entry["body_skipped"] = True
            skipped_rows += 1
        elif prev and prev.get("edited") and prev["edited"] == entry["edited"] \
                and ("body" in prev or not bodies):
            if "body" in prev:
                entry["body"] = prev["body"]
            reused += 1
        elif bodies:
            body, dropped = blockrender.render(api.fetch_blocks(page["id"]))
            for kind in dropped:
                dropped_kinds[kind] = dropped_kinds.get(kind, 0) + 1
            entry["body"] = body
            fetched += 1
        if api._same_minute(entry["edited"]):
            entry["edited"] = ""       # force one re-read next run (ADR-0015 §7)
        entry["body_chars"] = len((entry.get("body") or "").strip())
        entry["authorship"] = authorship(entry, decided)
        out[entry["page_id"]] = entry

    gone = len(set(old) - set(out))
    print(f"  notion: {len(out):,} page(s) — {fetched:,} body read, {reused:,} unchanged, "
          f"{skipped_rows:,} in skipped databases, {gone:,} gone since last sync")
    if dropped_kinds:
        detail = ", ".join(f"{k} x{n}" for k, n in sorted(dropped_kinds.items()))
        print(f"  block types with no markdown spelling, dropped: {detail}")
    return {"synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages": out}


def _write_json(name: str, blob) -> Path:
    p = cache_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


# ─────────────────────────────── the match ───────────────────────────────

_WS = re.compile(r"\s+")


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", s).strip()


def norm_body(s: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", s or "")).strip()


def _day(s: str) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def vault_notes(base: Path | None = None) -> list[dict]:
    """The vault's imported Apple Notes: origin_ref, title, created, folder, body."""
    from ..loaders.t1_index import _s, _split_frontmatter

    base = base or config.VAULT
    out = []
    for path in sorted((base / "raw" / "import").rglob("*.md")):
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        out.append({"origin_ref": str(path.relative_to(base)),
                    "title": _s(fm.get("title")) or path.stem,
                    "created": _s(fm.get("created")),
                    "folder": _s(fm.get("folder")),
                    "uuid": _s(fm.get("uuid")),
                    "body": body})
    return out


def match(pages: list[dict], notes: list[dict]) -> dict:
    """Pair pages with vault notes on (normalised title, created ±1 day).

    One-to-one only: a key two notes share, or a note two pages claim, is
    reported as ambiguous and left unmatched rather than guessed. A wrong
    adoption would hand one note's atoms to another.
    """
    by_title: dict[str, list[dict]] = {}
    for n in notes:
        by_title.setdefault(norm_title(n["title"]), []).append(n)

    def near(n, day):
        nd = _day(n["created"])
        return bool(day and nd and abs((nd - day).days) <= 1)

    pairs: dict[str, str] = {}
    ambiguous: list[dict] = []
    new: list[dict] = []
    claimed: dict[str, list[str]] = {}
    for p in pages:
        day = _day(p["created"])
        t = norm_title(p["title"])
        cands = [n for n in by_title.get(t, []) if near(n, day)]
        if not cands and t.endswith("..."):     # NFKC spells "…" as "..."
            # The Notion importer cut long titles; the vault kept them whole.
            stem = t[:-3].rstrip()
            cands = [n for n in notes if norm_title(n["title"]).startswith(stem) and near(n, day)]
        if len(cands) == 1:
            pairs[p["page_id"]] = cands[0]["origin_ref"]
            claimed.setdefault(cands[0]["origin_ref"], []).append(p["page_id"])
        elif cands:
            ambiguous.append({"page_id": p["page_id"], "title": p["title"],
                              "candidates": [c["origin_ref"] for c in cands]})
        else:
            new.append({"page_id": p["page_id"], "title": p["title"],
                        "created": p["created"], "folder": p.get("folder", "")})
    for ref, pids in claimed.items():
        if len(pids) > 1:
            for pid in pids:
                pairs.pop(pid, None)
                ambiguous.append({"page_id": pid, "candidates": [ref]})

    page_by_id = {p["page_id"]: p for p in pages}
    note_by_ref = {n["origin_ref"]: n for n in notes}
    folder_agree = sum(1 for pid, ref in pairs.items()
                       if page_by_id[pid].get("folder") == note_by_ref[ref]["folder"])
    with_body = [(pid, ref) for pid, ref in pairs.items() if "body" in page_by_id[pid]]
    body_same = sum(1 for pid, ref in with_body
                    if norm_body(page_by_id[pid]["body"]) == norm_body(note_by_ref[ref]["body"]))
    unmatched_notes = sorted(set(note_by_ref) - set(pairs.values()))
    return {
        "pages": len(pages), "vault_notes": len(notes),
        "matched": len(pairs), "ambiguous": len(ambiguous), "new": len(new),
        "vault_unmatched": len(unmatched_notes),
        "matched_folder_agrees": folder_agree,
        "matched_with_body": len(with_body), "matched_body_identical": body_same,
        "map": pairs,
        "ambiguous_pages": ambiguous, "new_pages": new,
        "vault_unmatched_refs": unmatched_notes,
    }


def review_list(pages: list[dict]) -> list[dict]:
    rows = [{"page_id": p["page_id"], "title": p["title"], "path": p.get("path", ""),
             "url": p.get("url", ""), "body_chars": p.get("body_chars", 0),
             "created_by": p["created_by"]["type"], "signals": suspicion(p)}
            for p in pages if p.get("authorship") == "unreviewed"]
    return sorted(rows, key=lambda r: (-len(r["signals"]), r["title"]))


def report(pages: list[dict], result: dict | None, review: list[dict]) -> None:
    min_chars = int(_cfg("min_body_chars", MIN_BODY_CHARS))
    have_body = [p for p in pages if "body" in p]
    writing = [p for p in have_body if p["body_chars"] >= min_chars]
    by: dict[str, int] = {}
    for p in pages:
        k = p["created_by"]["type"] or "?"
        by[k] = by.get(k, 0) + 1
    auth: dict[str, int] = {}
    for p in writing:
        auth[p["authorship"]] = auth.get(p["authorship"], 0) + 1
    print(f"  {len(pages):,} page(s); {len(have_body):,} with a body; "
          f"{len(writing):,} at or over {min_chars} chars (would be indexed)")
    print("  created_by: " + ", ".join(f"{k} {v:,}" for k, v in sorted(by.items())))
    print("  authorship of indexable pages: "
          + ", ".join(f"{k} {v:,}" for k, v in sorted(auth.items())))
    print(f"  review list: {len(review):,} page(s) the heuristic flagged, undecided")
    if result:
        r = result
        print(f"  archive vs vault raw/import: {r['pages']:,} page(s), {r['vault_notes']:,} note(s)")
        print(f"    adopt into an existing row   {r['matched']:>6,}   "
              f"(folder agrees on {r['matched_folder_agrees']:,})")
        if r["matched_with_body"]:
            print(f"      body identical after whitespace normalisation: "
                  f"{r['matched_body_identical']:,} of {r['matched_with_body']:,}")
        print(f"    ambiguous, left unmatched    {r['ambiguous']:>6,}")
        print(f"    new to the record            {r['new']:>6,}")
        print(f"    vault notes with no page     {r['vault_unmatched']:>6,}")


# ─────────────────────────────── entry ───────────────────────────────


def run(*, full: bool = False, bodies: bool = True) -> int:
    status = 0
    try:
        blob = sync(full=full, bodies=bodies)
    except (api.NotionError, RuntimeError) as exc:
        print(f"  fetch-notion-writing: {exc} — keeping the existing cache")
        status = 1
        blob = read_cache()
    else:
        print(f"  -> {_write_json('pages.json', blob)}")
    pages = list(blob.get("pages", {}).values())
    if not pages:
        return status or 1

    result = None
    archive = _ids("EXO_NOTION_WRITING_ADOPT", "adopt_databases")
    if archive and config.VAULT.exists():
        result = match([p for p in pages if p["database"] in archive], vault_notes())
        print(f"  -> {_write_json('match-report.json', result)}")
    review = review_list(pages)
    print(f"  -> {_write_json('authorship-review.json', review)}")
    report(pages, result, review)
    return status
