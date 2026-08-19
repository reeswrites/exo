"""
extract.py — Read the Apple Notes SQLite DB and decode note bodies.

The DB lives at:
  ~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite

Note bodies are stored as gzip-compressed protobuf blobs in ZICNOTEDATA.ZDATA.
We copy the DB (+ WAL / SHM) to a temp directory so we never touch the live
store and always see the latest committed state.

Protobuf wire format is decoded manually with stdlib only (no google.protobuf,
no protoc). The relevant path through the proto is:
  NoteStoreProto.document [field 2]
    → Document.note [field 3]
      → Note.note_text [field 2]   (the plain-text body)
      → Note.attribute_runs [field 5] (repeated, for structure hints)

AttributeRun fields used:
  length [field 1]      — number of characters this run covers
  paragraph_style [field 3]
    → ParagraphStyle.style_type [field 1]
      0   = title      → #
      1   = heading    → ##
      2   = subheading → ###
      100 = numbered   → 1.
      101 = dotList    → -
      103 = checklist  → - [ ]

NoteRecord is a plain dataclass returned to the rest of the pipeline.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Apple timestamps (seconds since 2001-01-01) ──────────────────────────────
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01

DB_PATH = Path(
    os.path.expanduser(
        "~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"
    )
)

# ── Folder exclusions ────────────────────────────────────────────────────────
# Folders that never enter the pipeline at all — not synced to raw/import, not
# atomized, not reported. This is the single "keep it out" gate: every consumer
# (sync, build, atomize, digest) draws notes through extract_notes, so a folder
# named here is invisible everywhere downstream. Distinct from atomize's idea
# ALLOWLIST (which only gates what becomes atoms); an excluded folder is gone.
# Matched by ANCESTRY (see folder_ancestry): excluding "Private" excludes every
# folder nested under it, so private material is corralled by moving its folder
# under Private in Apple Notes rather than listing each leaf here. "Recently
# Deleted" is always excluded (Apple's trash). Override the extra set with
# $NOTES_EXCLUDE_FOLDERS (comma-separated); defaults to Private.
_ALWAYS_EXCLUDED = {"Recently Deleted"}
EXCLUDED_FOLDERS = _ALWAYS_EXCLUDED | {
    f.strip()
    for f in os.environ.get("NOTES_EXCLUDE_FOLDERS", "Private").split(",")
    if f.strip()
}

NOTE_QUERY = """
SELECT
    n.Z_PK            AS id,
    n.ZTITLE1         AS title,
    n.ZIDENTIFIER     AS uuid,
    n.ZISPINNED       AS pinned,
    -- Creation date lives in different columns across Notes schema versions.
    -- ZCREATIONDATE3 is populated on current macOS; ZCREATIONDATE1/ZCREATIONDATE
    -- are older fallbacks. Without COALESCE, created_ts comes back NULL and the
    -- pipeline silently substitutes modification time (wrong; see vault ADR-0001).
    COALESCE(n.ZCREATIONDATE3, n.ZCREATIONDATE1, n.ZCREATIONDATE) AS created_ts,
    COALESCE(n.ZMODIFICATIONDATE1, n.ZMODIFICATIONDATE) AS modified_ts,
    f.ZTITLE2         AS folder,
    n.ZFOLDER         AS folder_pk,
    d.ZDATA           AS data,
    d.ZCRYPTOINITIALIZATIONVECTOR AS crypto_iv
FROM ZICCLOUDSYNCINGOBJECT n
LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK = n.ZFOLDER
LEFT JOIN ZICNOTEDATA d ON d.ZNOTE = n.Z_PK
WHERE
    n.ZTITLE1 IS NOT NULL
    AND (n.ZMARKEDFORDELETION IS NULL OR n.ZMARKEDFORDELETION != 1)
"""


@dataclass
class NoteRecord:
    id: int
    title: str
    uuid: str
    pinned: bool
    created: Optional[str]
    modified: Optional[str]
    folder: Optional[str]
    body: str                          # plain-text body (fallback)
    structured_body: str               # body with Markdown structure applied
    hashtags: list[str] = field(default_factory=list)
    explicit_link_uuids: list[str] = field(default_factory=list)  # applenotes: links
    skipped: bool = False
    skip_reason: str = ""


# ── Protobuf wire-format decoder (stdlib only) ────────────────────────────────

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for every top-level field in buf."""
    pos = 0
    while pos < len(buf):
        try:
            key, pos = _read_varint(buf, pos)
        except IndexError:
            break
        fn = key >> 3
        wt = key & 7
        try:
            if wt == 0:          # varint
                v, pos = _read_varint(buf, pos)
                yield fn, 0, v
            elif wt == 2:        # length-delimited
                ln, pos = _read_varint(buf, pos)
                yield fn, 2, buf[pos: pos + ln]
                pos += ln
            elif wt == 1:        # 64-bit
                yield fn, 1, buf[pos: pos + 8]
                pos += 8
            elif wt == 5:        # 32-bit
                yield fn, 5, buf[pos: pos + 4]
                pos += 4
            else:
                break            # unknown wire type — stop gracefully
        except Exception:
            break


def _first(buf: bytes, field_num: int, wire_type: int = 2):
    """Return the first value for a given field number (default: bytes)."""
    for fn, wt, v in _iter_fields(buf):
        if fn == field_num and wt == wire_type:
            return v
    return None


def _follow(buf: bytes, path: list[int]) -> Optional[bytes]:
    """Follow a chain of length-delimited fields and return the final bytes."""
    cur = buf
    for fnum in path:
        cur = _first(cur, fnum)
        if cur is None:
            return None
    return cur


# ── Structure extraction from attribute runs ──────────────────────────────────

_STYLE_MAP = {
    0: "title",
    1: "heading",
    2: "subheading",
    # 4 = monospaced. Deliberately unmapped: those paragraphs already hold
    # literal pasted Markdown ("## Methodology"); prefixing would corrupt it.
    100: "numbered",
    101: "dashed",
    102: "bullet",     # dotted list — 362 paragraphs, previously unmapped
    103: "checklist",
}

_STYLE_PREFIX = {
    "title": "# ",
    "heading": "## ",
    "subheading": "### ",
    "numbered": "1. ",
    "dashed": "- ",
    "bullet": "- ",
    "checklist": "- [ ] ",
}


def _parse_attribute_runs(note_bytes: bytes):
    """
    Parse AttributeRun fields (field 5 of Note proto) and return a list of
    (length_chars, style_name_or_None, block_quote_bool, link_uuid_or_None).

    ParagraphStyle is **field 2** of AttributeRun, not field 3. Field 3 is a
    varint that is always 1, so the old `rfn == 3 and rwt == 2` guard never
    matched and no paragraph style — heading, list, or quote — was ever decoded.
    Within ParagraphStyle: 1=style_type, 4=indent_amount, 8=block_quote.
    """
    runs = []
    for fn, wt, v in _iter_fields(note_bytes):
        if fn != 5 or wt != 2:
            continue
        run_len = None
        style = None
        block_quote = False
        link_uuid = None
        for rfn, rwt, rv in _iter_fields(v):
            if rfn == 1 and rwt == 0:       # length
                run_len = rv
            elif rfn == 2 and rwt == 2:     # paragraph_style
                st = _first(rv, 1, 0)       # style_type varint
                if st is not None:
                    style = _STYLE_MAP.get(st)
                block_quote = bool(_first(rv, 8, 0))   # Apple Notes "Block Quote"
            elif rfn == 5 and rwt == 2:     # NoteAttribute (links etc.)
                # field 5 inside NoteAttribute is the link URL bytes
                link_bytes = _first(rv, 5, 2)
                if link_bytes:
                    try:
                        url = link_bytes.decode("utf-8", "replace")
                        m = re.search(
                            r"applenotes:note/([A-Za-z0-9\-]+)", url
                        )
                        if m:
                            link_uuid = m.group(1)
                    except Exception:
                        pass
        if run_len is not None:
            runs.append((run_len, style, block_quote, link_uuid))
    return runs


def _apply_structure(text: str, runs: list) -> tuple[str, list[str]]:
    """
    Given plain text and attribute runs, return (structured_markdown, link_uuids).

    Apple Notes stores paragraph style on the run(s) that belong to a paragraph;
    the paragraph boundary is the newline embedded in the plain text.  Multiple
    short runs (character-level bold/italic/link formatting) can cover a single
    paragraph — we must NOT add newlines between runs.

    Algorithm (two-pass):
      Pass 1 — walk runs to map paragraph-start character positions to their style,
               and collect any explicit note-link UUIDs.
      Pass 2 — walk text character-by-character, inserting Markdown prefixes only
               at the very start of paragraphs that have a structural style.

    Falls back to the original plain text if anything goes wrong.
    """
    link_uuids: list[str] = []
    if not runs:
        return text, link_uuids

    try:
        # ── Pass 1: build para_start_pos → (style, block_quote) mapping ───────
        para_styles: dict[int, tuple[Optional[str], bool]] = {}
        pos = 0
        para_start = 0
        last_style: Optional[str] = None
        last_quote = False

        for run_len, style, block_quote, link_uuid in runs:
            if link_uuid:
                link_uuids.append(link_uuid)
            if style:
                last_style = style
            if block_quote:
                last_quote = True
            # Scan this run's characters for paragraph-ending newlines
            end = pos + run_len
            chunk = text[pos:end]
            for ci, ch in enumerate(chunk):
                if ch == "\n":
                    if last_style or last_quote:
                        para_styles[para_start] = (last_style, last_quote)
                    para_start = pos + ci + 1
                    last_style = None
                    last_quote = False
            pos = end

        # Handle last paragraph (may have no trailing newline)
        if (last_style or last_quote) and para_start < len(text):
            para_styles[para_start] = (last_style, last_quote)

        # ── Pass 2: rebuild text with Markdown prefixes ───────────────────────
        # Block quote wins the outer position: "> - item", never "- > item",
        # so a quoted list still reads as quoted (ADR-0003 — the marker is the
        # only record that these bytes were quoted, not authored).
        parts: list[str] = []
        para_start = 0
        for i, ch in enumerate(text):
            if i == para_start:
                style_name, quoted = para_styles.get(para_start, (None, False))
                prefix = _STYLE_PREFIX.get(style_name, "") if style_name else ""
                if quoted:
                    prefix = "> " + prefix
                if prefix:
                    parts.append(prefix)
            parts.append(ch)
            if ch == "\n":
                para_start = i + 1

        return "".join(parts), link_uuids

    except Exception:
        return text, link_uuids


# ── Main decode function ──────────────────────────────────────────────────────

def _decode_note_data(blob: bytes) -> tuple[str, str, list[str]]:
    """
    Decode a ZDATA blob → (plain_body, structured_body, link_uuids).
    Returns ("", "", []) if decoding fails.
    """
    try:
        raw = gzip.decompress(blob)
    except Exception:
        return "", "", []

    # path: NoteStoreProto.document(2) → Document.note(3) → Note
    note_bytes = _follow(raw, [2, 3])
    if note_bytes is None:
        return "", "", []

    # plain text
    text_bytes = _first(note_bytes, 2)
    if text_bytes is None:
        return "", "", []
    plain = text_bytes.decode("utf-8", "replace")

    # structure from attribute runs
    runs = _parse_attribute_runs(note_bytes)
    structured, link_uuids = _apply_structure(plain, runs)

    return plain, structured, link_uuids


# ── Timestamp helper ──────────────────────────────────────────────────────────

def _apple_ts(val) -> Optional[str]:
    if val is None:
        return None
    try:
        ts = float(val) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    except Exception:
        return None


# ── Hashtag extractor ─────────────────────────────────────────────────────────

_HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_\-]*)")


def _extract_hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for m in _HASHTAG_RE.findall(text)))


# ── Public API ────────────────────────────────────────────────────────────────

def extract_notes(db_path: Path = DB_PATH) -> tuple[list[NoteRecord], list[dict]]:
    """
    Extract all non-deleted, non-encrypted notes from the Apple Notes DB.

    Returns:
        notes   — list of NoteRecord (successfully parsed)
        skipped — list of dicts describing notes that were skipped
    """
    # Copy the DB + WAL + SHM to a temp dir to avoid touching the live store
    tmp = tempfile.mkdtemp(prefix="note_compile_")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db_path) + suffix)
            if src.exists():
                shutil.copy2(src, Path(tmp) / src.name)

        conn = sqlite3.connect(
            Path(tmp) / db_path.name, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        rows = conn.execute(NOTE_QUERY).fetchall()
        # Folder tree, so exclusion can match by ANCESTRY: nesting People under
        # Private (Apple Notes) leaves the note's leaf folder = "People", but its
        # chain is Private ▸ People — excluding "Private" should catch it.
        folder_rows = conn.execute(
            "SELECT Z_PK, ZTITLE2, ZPARENT FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE2 IS NOT NULL"
        ).fetchall()
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _fname = {r["Z_PK"]: r["ZTITLE2"] for r in folder_rows}
    _fparent = {r["Z_PK"]: r["ZPARENT"] for r in folder_rows}

    def folder_ancestry(pk) -> set[str]:
        """Every folder name from the note's leaf up to the root (leaf included)."""
        names: set[str] = set()
        seen: set = set()
        while pk in _fname and pk not in seen:
            seen.add(pk)
            names.add(_fname[pk])
            pk = _fparent.get(pk)
        return names

    notes: list[NoteRecord] = []
    skipped: list[dict] = []

    for row in rows:
        nid   = row["id"]
        title = row["title"] or ""
        uuid  = row["uuid"] or ""
        folder = row["folder"] or "Unfiled"

        # Skip excluded folders by ANCESTRY (Recently Deleted + configured, e.g.
        # Private) — a note anywhere under an excluded folder is excluded.
        hit = EXCLUDED_FOLDERS & folder_ancestry(row["folder_pk"])
        if hit:
            reason = ("recently_deleted" if "Recently Deleted" in hit
                      else f"excluded_folder:{sorted(hit)[0]}")
            skipped.append({"id": nid, "title": title, "reason": reason})
            continue

        # Skip encrypted
        if row["crypto_iv"] is not None:
            skipped.append({"id": nid, "title": title, "reason": "encrypted"})
            continue

        # Skip notes with no data blob
        blob = row["data"]
        if not blob:
            skipped.append({"id": nid, "title": title, "reason": "no_data"})
            continue

        plain, structured, link_uuids = _decode_note_data(bytes(blob))

        if not plain.strip() and not title.strip():
            skipped.append({"id": nid, "title": title, "reason": "empty_body"})
            continue

        hashtags = _extract_hashtags(plain)

        notes.append(
            NoteRecord(
                id=nid,
                title=title,
                uuid=uuid,
                pinned=bool(row["pinned"]),
                created=_apple_ts(row["created_ts"]),
                modified=_apple_ts(row["modified_ts"]),
                folder=folder,
                body=plain,
                structured_body=structured,
                hashtags=hashtags,
                explicit_link_uuids=link_uuids,
            )
        )

    return notes, skipped
