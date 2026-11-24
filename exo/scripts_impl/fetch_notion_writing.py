"""`exo fetch-notion-writing` — the owner's Notion writing, cached for indexing.

Notion is the source of truth for the owner's writing now; Exo's copy is an
index that exists to be searched by meaning (`notes_on`, `whats_relevant`),
which Notion's own keyword search cannot do. This command is the first slice of
that: it READS the configured Notion databases into a cache under `raw/` and
reports how the pages line up with the notes the record already holds. It
changes no loader and no zone.

Why a cache and a report before any loader change: ~1,900 of those pages are
the Apple Notes archive, which the vault ALREADY holds as `raw/import/*.md` with
`source = "second-brain"`. Atoms hash `origin_ref`, and alchemy and the collider
reference note ids, so pulling the archive in as new `notion` rows would mint a
second copy of every note and orphan everything built on the first. The next
slice has to adopt each page into the row it already is. This one measures
whether that is possible, and writes the page -> origin_ref map it would use.

The match key is (normalised title, created date ±1 day). The obvious key does
not exist: Notion's "Apple Notes ID" is the Core Data URI
(`x-coredata://…/ICNote/p659`) the Notion importer read, while the vault's
`uuid` is the note's ZIDENTIFIER (`2FCEB207-…`) from a different exporter. Same
notes, two id systems, no column holding both.

Incremental: the property query runs in full every time (one request per 100
pages, ~20 for the archive) because it is also how deletions are seen. Blocks
are fetched only for pages whose `last_edited_time` moved — that is the
expensive part, ~1 request per page. A page edited in the same minute as the
read is re-read next run (ADR-0015 §7, `api._same_minute`).

Config: `[notion_writing] databases = ["<database id>", …]` in exo.toml, or
EXO_NOTION_WRITING_DATABASES (comma-separated). Needs NOTION_TOKEN, with the
integration connected to those databases in Notion.
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

# A run may drop at most this share of the pages the cache held. A deleted page
# is an edit; a tenth of the archive vanishing in one query is a disconnected
# integration or a truncated response.
MAX_DROP = 0.10


def cache_dir() -> Path:
    return config.ROOT / "raw" / "notion-writing"


def databases() -> list[str]:
    env = os.environ.get("EXO_NOTION_WRITING_DATABASES", "").strip()
    if env:
        return [d.strip() for d in env.split(",") if d.strip()]
    return list(config.setting("notion_writing", "databases", []) or [])


# ─────────────────────────────── one page ───────────────────────────────


def _prop(page: dict, name: str) -> dict:
    return (page.get("properties") or {}).get(name) or {}


def _date(page: dict, name: str) -> str:
    return ((_prop(page, name).get("date") or {}).get("start") or "").strip()


def _check(page: dict, name: str) -> bool:
    return bool(_prop(page, name).get("checkbox"))


def shape(page: dict) -> dict:
    """The properties this index keeps, without the body."""
    folder = (_prop(page, "Folder").get("select") or {}).get("name", "")
    return {
        "page_id": api._norm_id(page.get("id", "")),
        "title": api.title_of(page).strip(),
        "folder": folder,
        # The Apple Notes date when the page carries one; Notion's own
        # created_time is the import date for the whole archive (2026-08-20).
        "created": _date(page, "Created") or page.get("created_time", ""),
        "modified": _date(page, "Modified"),
        "apple_notes_id": api._prop_text(_prop(page, "Apple Notes ID")).strip(),
        "locked": _check(page, "Locked"),
        "skip": _check(page, "Skip?"),
        "published": _check(page, "Published?"),
        "edited": page.get("last_edited_time", ""),
        "url": page.get("url", ""),
    }


# ─────────────────────────────── the cache ───────────────────────────────


def _cache_path(database: str) -> Path:
    return cache_dir() / f"{api._norm_id(database)}.json"


def read_cache(database: str) -> dict:
    p = _cache_path(database)
    if not p.exists():
        return {}
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def sync(database: str, *, full: bool = False, bodies: bool = True) -> dict:
    """Query one database, fetch changed bodies, return the new cache blob.

    Raises RuntimeError when the result would shrink the cache past MAX_DROP.
    """
    old = {} if full else read_cache(database).get("pages", {})
    pages = [p for p in api._paged(f"/databases/{api._norm_id(database)}/query",
                                   method="POST", body={})
             if not (p.get("archived") or p.get("in_trash"))]
    if old and len(pages) < int(len(old) * (1 - MAX_DROP)):
        raise RuntimeError(
            f"{len(pages):,} pages, but the cache held {len(old):,} — more than "
            f"{MAX_DROP:.0%} gone in one query; refusing to write")
    if not pages:
        raise RuntimeError("the query returned no pages — refusing to write")

    out: dict[str, dict] = {}
    fetched = reused = 0
    skipped_kinds: dict[str, int] = {}
    for page in pages:
        entry = shape(page)
        prev = old.get(entry["page_id"])
        if prev and prev.get("edited") and prev["edited"] == entry["edited"] \
                and ("body" in prev or not bodies):
            out[entry["page_id"]] = {**entry, **({"body": prev["body"]} if "body" in prev else {})}
            reused += 1
            continue
        if bodies:
            body, skipped = blockrender.render(api.fetch_blocks(page["id"]))
            for kind in skipped:
                skipped_kinds[kind] = skipped_kinds.get(kind, 0) + 1
            entry["body"] = body
            fetched += 1
        if api._same_minute(entry["edited"]):
            entry["edited"] = ""       # force one re-read next run
        out[entry["page_id"]] = entry

    gone = len(set(old) - set(out))
    print(f"  {api._norm_id(database)}: {len(out):,} page(s) — {fetched:,} body read, "
          f"{reused:,} unchanged, {gone:,} gone since last sync")
    if skipped_kinds:
        detail = ", ".join(f"{k} x{n}" for k, n in sorted(skipped_kinds.items()))
        print(f"  block types with no markdown spelling, dropped: {detail}")
    return {"database": api._norm_id(database),
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages": out}


def write(database: str, blob: dict) -> Path:
    p = _cache_path(database)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
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
    from ..loaders.t1_index import _split_frontmatter, _s

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

    pairs: dict[str, str] = {}
    ambiguous: list[dict] = []
    new: list[dict] = []
    claimed: dict[str, list[str]] = {}
    def near(n, day):
        nd = _day(n["created"])
        return bool(day and nd and abs((nd - day).days) <= 1)

    for p in pages:
        day = _day(p["created"])
        t = norm_title(p["title"])
        cands = [n for n in by_title.get(t, []) if near(n, day)]
        if not cands and t.endswith("..."):     # NFKC spells "\u2026" as "..."
            # The Notion importer cut long titles and appended an ellipsis; the
            # vault kept them whole. Prefix plus the date, still one-to-one.
            stem = t[:-3].rstrip()
            cands = [n for n in notes
                     if norm_title(n["title"]).startswith(stem) and near(n, day)]
        if len(cands) == 1:
            pairs[p["page_id"]] = cands[0]["origin_ref"]
            claimed.setdefault(cands[0]["origin_ref"], []).append(p["page_id"])
        elif cands:
            ambiguous.append({"page_id": p["page_id"], "title": p["title"],
                              "candidates": [c["origin_ref"] for c in cands]})
        else:
            new.append({"page_id": p["page_id"], "title": p["title"],
                        "created": p["created"], "folder": p["folder"]})
    for ref, pids in claimed.items():
        if len(pids) > 1:
            for pid in pids:
                pairs.pop(pid, None)
                ambiguous.append({"page_id": pid, "candidates": [ref]})

    page_by_id = {p["page_id"]: p for p in pages}
    note_by_ref = {n["origin_ref"]: n for n in notes}
    folder_agree = sum(1 for pid, ref in pairs.items()
                       if page_by_id[pid]["folder"] == note_by_ref[ref]["folder"])
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


def report(result: dict) -> None:
    r = result
    print(f"  match vs vault raw/import: {r['pages']:,} page(s), {r['vault_notes']:,} vault note(s)")
    print(f"    adopt into an existing row   {r['matched']:>6,}   "
          f"(folder agrees on {r['matched_folder_agrees']:,})")
    if r["matched_with_body"]:
        print(f"      body identical after whitespace normalisation: "
              f"{r['matched_body_identical']:,} of {r['matched_with_body']:,}")
    print(f"    ambiguous, left unmatched    {r['ambiguous']:>6,}")
    print(f"    new to the record            {r['new']:>6,}")
    print(f"    vault notes with no page     {r['vault_unmatched']:>6,}")


# ─────────────────────────────── entry ───────────────────────────────


def run(*, full: bool = False, bodies: bool = True, database: list[str] | None = None) -> int:
    dbs = database or databases()
    if not dbs:
        print("fetch-notion-writing: no databases configured — set [notion_writing] "
              "databases in exo.toml or EXO_NOTION_WRITING_DATABASES")
        return 1
    status = 0
    all_pages: list[dict] = []
    for db in dbs:
        try:
            blob = sync(db, full=full, bodies=bodies)
        except (api.NotionError, RuntimeError) as exc:
            print(f"  {api._norm_id(db)}: {exc} — keeping the existing cache")
            status = 1
            blob = read_cache(db)
        else:
            print(f"  -> {write(db, blob)}")
        all_pages.extend(blob.get("pages", {}).values())

    if all_pages and config.VAULT.exists():
        result = match(all_pages, vault_notes())
        report(result)
        out = cache_dir() / "match-report.json"
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  -> {out}")
    return status
