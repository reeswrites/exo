"""Notes ingestion — the contract, the writer, and the three shipped adapters.

Every fixture here is written by hand in a tmp_path (CONTRIBUTING: no personal
data, ever, including in a fixture). What is being tested is the *shape* of an
input, which is all a loader can be tested against anyway.
"""
from __future__ import annotations

import json
import zipfile

import pytest
import yaml

from exo import notes as notes_mod
from exo.notes import SourceNote


def _read_fm(path):
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---")[1]), text.split("---", 2)[2].strip()


def _land(tmp_path, items, source="files", landing="files"):
    return notes_mod.land(items, source, landing, root=tmp_path)


def _landed(tmp_path, landing="files"):
    return sorted((tmp_path / "raw" / landing).glob("*.md"))


# ────────────────────────────── the writer ──────────────────────────────


def test_landing_is_idempotent(tmp_path):
    items = [SourceNote(external_id="a", title="One", body="first body", created="2026-01-02"),
             SourceNote(external_id="b", title="Two", body="second body", created="2026-01-03")]
    first = _land(tmp_path, items)
    assert first == {"new": 2, "changed": 0, "same": 0, "skipped": 0}
    second = _land(tmp_path, items)
    assert second == {"new": 0, "changed": 0, "same": 2, "skipped": 0}
    assert len(_landed(tmp_path)) == 2


def test_an_edited_note_is_overwritten_in_place_not_landed_twice(tmp_path):
    _land(tmp_path, [SourceNote(external_id="a", title="One", body="v1", created="2026-01-02")])
    counts = _land(tmp_path,
                   [SourceNote(external_id="a", title="One", body="v2", created="2026-01-02")])
    assert counts["changed"] == 1 and counts["new"] == 0
    files = _landed(tmp_path)
    assert len(files) == 1
    assert _read_fm(files[0])[1] == "v2"


def test_a_retitled_note_keeps_its_file(tmp_path):
    """Identity is the uuid, not the filename. A note renamed upstream must not
    land a second time — that is how a corpus doubles without anyone noticing."""
    _land(tmp_path, [SourceNote(external_id="a", title="Old", body="b", created="2026-01-02")])
    _land(tmp_path, [SourceNote(external_id="a", title="New", body="b", created="2026-01-02")])
    files = _landed(tmp_path)
    assert len(files) == 1
    assert files[0].name == "2026-01-02-old.md"        # the original name
    assert _read_fm(files[0])[0]["title"] == "New"     # the new title


def test_a_new_import_date_alone_is_not_a_change(tmp_path):
    """`imported:` moves every day. Counted as a change, every note rewrites
    itself on the first run of each day — which makes the tree's mtimes
    meaningless and re-uploads the whole mirror nightly."""
    note = SourceNote(external_id="a", title="One", body="b", created="2026-01-02")
    _land(tmp_path, [note])
    path = _landed(tmp_path)[0]
    path.write_text(path.read_text().replace("imported: ", "imported: 1999-01-01 #"), "utf-8")
    stale = path.read_text()
    assert _land(tmp_path, [note])["same"] == 1
    assert path.read_text() == stale                   # untouched, not rewritten


def test_two_notes_claiming_one_id_is_an_error(tmp_path):
    """Silently, the second overwrites the first and both counts read healthy."""
    with pytest.raises(ValueError, match="external_id"):
        _land(tmp_path, [SourceNote(external_id="a", title="One", body="x"),
                         SourceNote(external_id="a", title="Two", body="y")])


def test_a_note_that_is_not_text_never_lands(tmp_path):
    """A NUL means whatever was read is not text. D1 accepts a statement
    containing one, reports success and leaves the table empty, so it has to be
    refused at the door rather than three passes downstream."""
    counts = _land(tmp_path, [SourceNote(external_id="a", title="t", body="bad\x00bytes"),
                              SourceNote(external_id="b", title="t", body="   ")])
    assert counts == {"new": 0, "changed": 0, "same": 0, "skipped": 2}
    assert _landed(tmp_path) == []


# ────────────────────────────── files ──────────────────────────────


def _tree(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "loose.md").write_text("# Loose thought\n\nat the root, so unfiled\n", "utf-8")
    (root / "Reading").mkdir()
    (root / "Reading" / "2026-02-03-on-caching.md").write_text(
        "a note whose date is in its filename\n", "utf-8")
    (root / "Reading" / "declared.md").write_text(
        "---\ntitle: A Declared Title\ncreated: 2020-05-06\nfolder: Elsewhere\n---\n\nbody\n",
        "utf-8")
    (root / "Reading" / "plain.txt").write_text("a text file is a note too\n", "utf-8")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "config.md").write_text("machinery\n", "utf-8")
    (root / "Reading" / "._sidecar.md").write_bytes(b"\x00\x05AppleDouble")
    (root / "Reading" / "photo.png").write_bytes(b"\x89PNG\x00\x00")
    return root


def test_files_adapter_reads_a_tree(tmp_path):
    from exo.notes.sources import files
    got = {n.external_id: n for n in files.read(str(_tree(tmp_path / "w")))}
    assert set(got) == {"loose.md", "Reading/2026-02-03-on-caching.md",
                        "Reading/declared.md", "Reading/plain.txt"}

    # a file at the root has no folder, and unfiled is held (ADR-0009)
    assert got["loose.md"].folder == ""
    assert got["loose.md"].title == "Loose thought"          # from the heading

    dated = got["Reading/2026-02-03-on-caching.md"]
    assert dated.folder == "Reading"
    assert dated.created == "2026-02-03"                     # from the filename
    assert dated.title == "on caching"                       # date prefix is not the title

    declared = got["Reading/declared.md"]
    assert declared.created == "2020-05-06"                  # frontmatter wins over the path
    assert declared.folder == "Elsewhere"                    # and over the directory
    assert declared.title == "A Declared Title"


def test_files_adapter_refuses_what_is_not_writing(tmp_path):
    """A pointed-at directory has not been curated. A .png is not a note, and an
    AppleDouble sidecar is a binary that matches *.md."""
    from exo.notes.sources import files
    ids = {n.external_id for n in files.read(str(_tree(tmp_path / "w")))}
    assert not any(i.endswith(".png") for i in ids)
    assert "Reading/._sidecar.md" not in ids
    assert ".obsidian/config.md" not in ids


def test_files_adapter_reads_one_file(tmp_path):
    from exo.notes.sources import files
    p = tmp_path / "single.md"
    p.write_text("# Just this\n\nbody\n", "utf-8")
    got = files.read(str(p))
    assert len(got) == 1 and got[0].title == "Just this" and got[0].folder == ""


def test_files_adapter_needs_somewhere_to_read_from():
    from exo.notes.sources import files
    with pytest.raises(ValueError, match="--from"):
        files.read(None)


# ────────────────────────────── notion ──────────────────────────────

_ID_A = "0123456789abcdef0123456789abcdef"
_ID_B = "fedcba9876543210fedcba9876543210"
_ID_C = "aaaabbbbccccddddeeeeffff00001111"


def _notion_export(root):
    """The shape Notion's "Export -> Markdown & CSV" writes, by hand."""
    wrapper = root / f"Export-{_ID_A}"
    (wrapper / f"Reading {_ID_A}").mkdir(parents=True)
    (wrapper / f"Reading {_ID_A}.md").write_text(
        f"# Reading\n\nCreated: August 1, 2026\nTags: books, notes\n\nthe parent page\n", "utf-8")
    (wrapper / f"Reading {_ID_A}" / f"On Caching {_ID_B}.md").write_text(
        "# On Caching\n\nCreated: 2026-02-03\n\nthe child page body\n", "utf-8")
    (wrapper / f"Reading {_ID_A}" / f"Sources {_ID_C}.csv").write_text(
        "Name,Author\nOne,Two\n", "utf-8")
    (wrapper / f"Reading {_ID_A}" / "Untagged page.md").write_text(
        "# Untagged page\n\nno id in the filename\n", "utf-8")
    return root


def test_notion_adapter_reads_an_unpacked_export(tmp_path, capsys):
    from exo.notes.sources import notion
    got = {n.external_id: n for n in notion.read(str(_notion_export(tmp_path / "e")))}

    # the wrapper directory is descended past — it carries a per-export uuid, so
    # leaving it in would expire every folder decision on the next export
    assert got[_ID_A].folder == ""
    assert got[_ID_A].title == "Reading"
    assert got[_ID_A].created == "2026-08-01"           # "August 1, 2026"

    child = got[_ID_B]
    assert child.folder == "Reading"                    # the page hierarchy, id stripped
    assert child.created == "2026-02-03"
    assert "the child page body" in child.body

    # the database index is skipped; its rows are markdown beside it
    assert not any(n.title == "Sources" for n in got.values())

    # a page with no id falls back to its path, loudly
    fallback = [k for k in got if k.startswith("path:")]
    assert len(fallback) == 1
    assert "carry no id" in capsys.readouterr().out


def test_notion_adapter_keeps_the_property_block_in_the_body(tmp_path):
    """Those values are content a person can see in Notion. Dropping them makes
    the import lossy; the atomizer discards a span that short anyway."""
    from exo.notes.sources import notion
    got = {n.external_id: n for n in notion.read(str(_notion_export(tmp_path / "e")))}
    assert "Tags: books, notes" in got[_ID_A].body


def test_notion_adapter_reads_the_zip_notion_actually_hands_you(tmp_path):
    from exo.notes.sources import notion
    src = _notion_export(tmp_path / "e")
    archive = tmp_path / "Export.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    ids = {n.external_id for n in notion.read(str(archive))}
    assert _ID_A in ids and _ID_B in ids


def test_notion_adapter_refuses_an_escaping_zip_entry(tmp_path):
    """The archive is a download."""
    from exo.notes.sources import notion
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../escaped.md", "# nope\n")
    with pytest.raises(ValueError, match="outside the archive"):
        notion.read(str(archive))


# ─────────────────────── landing is a publication decision ───────────────────


def test_every_shipped_adapter_lands_somewhere_of_its_own():
    from exo.notes import sources
    landings = [getattr(sources.get(n), "LANDING") for n in sources.BUILTIN]
    assert len(set(landings)) == len(landings)


def test_a_new_source_lands_under_no_declared_path_zone():
    """The fail-closed hook. `_zone_of` matches the LONGEST declared prefix, so a
    source landing inside `raw/import` would inherit that zone's serve decision
    on its very first run. Landing at `raw/<source>/` matches nothing instead,
    and the next publish stops with "note(s) under no declared path_zone" —
    which is the correct answer to a question nobody was asked.
    """
    from pathlib import Path
    from exo.scripts_impl.publish import _zone_of
    from exo.notes import sources

    template = json.loads(
        (Path(__file__).resolve().parent.parent
         / "exo" / "templates" / "serve-manifest.json").read_text())
    zones = template["path_zones"]

    for name in sources.BUILTIN:
        landing = getattr(sources.get(name), "LANDING")
        ref = f"raw/{landing}/a-note.md"
        declared = _zone_of(ref, zones)
        # apple keeps `raw/import` for stored identity and is declared there;
        # anything new must resolve to nothing at all.
        assert declared in (None, f"raw/{landing}"), (
            f"{name} lands in raw/{landing}, which inherits the decision made "
            f"about {declared!r} — a source must carry its own")
