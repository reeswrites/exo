"""Notion writing → t1_notes. The index half of `exo fetch-notion-writing`.

Notion is where the owner writes now; Exo keeps a rebuildable copy to search by
meaning. The fetcher leaves `raw/notion-writing/pages.json` — every page the
integration sees, with a rendered body and an authorship verdict. This folds it
into the note rows `t1_index.notes()` has already read from the vault, and does
nothing at all when that file is absent, so an instance that never fetched is
unchanged.

What it does to the record:

  adopt       An Apple Notes archive page matched to a vault note (fetch's
              `match`) enriches that row and never replaces its identity: same
              `source`, same `origin_ref`, same vault folder, same id. The body
              stays the vault's unless the page was edited in Notion after the
              import (someone other than its creator last touched it) AND the
              text actually differs — the importer's markdown differs from the
              vault's in spelling alone (`_x_` vs `*x*`), and swapping that in
              would re-mint every id for nothing.

  versions    A vault note re-imported after an edit sits beside the original as
              `-v2`, `-v3` … under one uuid. Only the newest stays in the index;
              the rest stay on disk and stop being served.

  new         Every other page with a body of at least `min_body_chars`, outside
              the excluded subtrees (`exclude`, e.g. Private), becomes a row of
              its own: `source = "notion-writing"`, `origin_ref =
              raw/notion-writing/<page_id>`. That prefix is a path zone no
              manifest declares until the owner does, so the first publish
              after this fails closed instead of serving pages nobody reviewed.
              Archive pages keep their Apple Notes folder; other pages have
              none, which is the held, unfiled drawer.

  authorship  `payload.authorship` carries the fetch verdict (human / mixed /
              unreviewed / ai, "" where Notion has no say). A page marked ai is
              `author = "machine"`, `grounds = False`: it is not his evidence.
              The column is left out of the id hash — adding a payload column
              otherwise re-mints every note id (CONTRIBUTING), and a reviewer's
              verdict changing is not the note changing.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .. import config
from ..provenance import Row, stable_id
from ..scripts_impl import fetch_notion_writing as fnw

SOURCE = "notion-writing"


def note_id(source: str, payload: dict) -> str:
    """The note id, as it was before `authorship`, `link` and `notion_id` existed.

    All three are pointers ABOUT a note rather than its content, and adding any
    must not re-mint every Notion note's id (first_seen is keyed on it)."""
    return stable_id(source, "note",
                     *[payload[k] for k in sorted(payload) if k not in _NOT_IDENTITY])


_NOT_IDENTITY = {"authorship", "link", "notion_id"}


def _loose(s: str) -> str:
    """Text with markdown spelling and whitespace removed, for 'did it change'."""
    s = unicodedata.normalize("NFKC", s or "").casefold().lstrip()
    if s.startswith("# "):                 # the vault opens on its title; Notion doesn't
        s = s.split("\n", 1)[1] if "\n" in s else ""
    s = re.sub(r"^\s*\d+\.\s|[#*_>`~\-\[\]()|]", " ", s, flags=re.M)
    return re.sub(r"\s+", " ", s).strip()


def _edited_in_notion(page: dict) -> bool:
    a = (page.get("created_by") or {}).get("id")
    b = (page.get("last_edited_by") or {}).get("id")
    return bool(a and b and a != b)


def load(path: Path | None = None) -> dict | None:
    p = path or (fnw.cache_dir() / "pages.json")
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _rebuild(row: Row, payload: dict, *, author: str | None = None,
             grounds: bool | None = None) -> Row:
    return Row(tier=row.tier, zone=row.zone, source=row.source,
               author=author or row.author, payload=payload, created=row.created,
               origin_ref=row.origin_ref,
               grounds=row.grounds if grounds is None else grounds,
               id=note_id(row.source, payload))


def _envelope(verdict: str) -> tuple[str, bool]:
    return ("machine", False) if verdict == "ai" else ("human", True)


def overlay(rows: list[Row], cache: dict | None = None) -> list[Row]:
    cache = cache if cache is not None else load()
    if cache is None:
        return rows
    pages = list((cache.get("pages") or {}).values())
    decided = fnw.decisions()
    for p in pages:
        if "body" in p:
            p["authorship"] = fnw.authorship(p, decided)
    archive = fnw._ids("EXO_NOTION_WRITING_ADOPT", "adopt_databases")
    exclude = fnw._names("EXO_NOTION_WRITING_EXCLUDE", "exclude")
    min_chars = int(fnw._cfg("min_body_chars", fnw.MIN_BODY_CHARS))

    vault = [r for r in rows if r.source == "second-brain"
             and r.origin_ref.startswith("raw/import/")]
    notes = [{"origin_ref": r.origin_ref, "title": r.payload.get("title", ""),
              "created": r.created or "", "folder": r.payload.get("folder", ""),
              "uuid": r.payload.get("uuid", ""), "body": r.payload.get("body", ""),
              **_version(r)} for r in vault]
    archive_pages = [p for p in pages if p.get("database") in archive]
    result = fnw.match(archive_pages, notes) if archive_pages else \
        {"map": {}, "ambiguous_pages": [], "superseded_refs": sorted(fnw.newest(notes)[1])}
    adopted = {ref: pid for pid, ref in result["map"].items()}
    by_id = {p["page_id"]: p for p in pages}
    gone = set(result["superseded_refs"])
    held_back = {a["page_id"] for a in result["ambiguous_pages"]}

    out: list[Row] = []
    for r in rows:
        if r.origin_ref in gone:
            continue
        if r.zone != "note":
            out.append(r)
            continue
        pid = adopted.get(r.origin_ref)
        page = by_id.get(pid) if pid else None
        payload = dict(r.payload)
        payload["authorship"] = (page or {}).get("authorship", "") if page and "body" in page else ""
        # The Notion page this vault note was adopted onto, so a hit on it (or on
        # any atom cut from it) can be opened where the note now lives. Empty for
        # a vault note Notion never received.
        payload["notion_id"] = pid or ""
        if page and "body" in page and _edited_in_notion(page) \
                and _loose(page["body"]) != _loose(payload.get("body", "")):
            payload["body"] = page["body"]
        author, grounds = _envelope(payload["authorship"]) if payload["authorship"] \
            else (r.author, r.grounds)
        out.append(_rebuild(r, payload, author=author, grounds=grounds))

    taken = set(result["map"]) | held_back
    for p in sorted(pages, key=lambda p: p["page_id"]):
        if p["page_id"] in taken or "body" not in p:
            continue
        if p.get("body_chars", len(p["body"])) < min_chars or fnw.under(p, exclude):
            continue
        is_archive = p.get("database") in archive
        author, grounds = _envelope(p["authorship"])
        payload = {
            "title": p.get("title", ""),
            "note_type": "raw",
            "voice": "",
            "facet": "",
            "folder": p.get("folder", "") if is_archive else "",
            "uuid": p["page_id"],
            "body": p["body"],
            "authorship": p["authorship"],
            "link": p.get("chat_url", ""),
            # The Notion page id: `uuid` already is one for these rows, but
            # vault notes carry theirs in `notion_id`, so every note answers the
            # same question in the same column.
            "notion_id": p["page_id"],
        }
        out.append(Row(tier="t1", zone="note", source=SOURCE, author=author,
                       payload=payload, created=(p.get("created") or "")[:10] or None,
                       origin_ref=f"raw/notion-writing/{p['page_id']}",
                       grounds=grounds, id=note_id(SOURCE, payload)))
    return out


def _version(r: Row) -> dict:
    """`modified` / `version` live in the vault file's frontmatter, not the row."""
    try:
        from .t1_index import _split_frontmatter
        fm, _ = _split_frontmatter((config.VAULT / r.origin_ref).read_text(
            encoding="utf-8", errors="replace"))
    except OSError:
        return {"modified": "", "version": 1}
    v = str(fm.get("version") or "1")
    return {"modified": str(fm.get("modified") or ""), "version": int(v) if v.isdigit() else 1}
