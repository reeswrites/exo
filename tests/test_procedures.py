"""t1_procedure — the zone that returns text an assistant is meant to act on.

Every check here is about who may write and what may leave, because those are
the only two questions this zone raises that the descriptive zones do not
(ADR-0016).

The invalid procedures are written into tmp_path rather than committed under
`fixtures/instance/procedures/`, and that is deliberate: the loader FAILS the
build on a bad file, so a committed oversized fixture would make
`EXO_HOME=fixtures/instance exo rebuild` impossible — the fixture instance has
to stay buildable. The two valid ones are committed, one served and one held, so
the publication path has real rows of both kinds to run against. Same idiom as
test_projects.py, which builds its git repos on the fly.
"""
from __future__ import annotations

import json

import pytest

from exo import config, toolzones
from exo.loaders import procedure
from exo.scripts_impl import publish

GOOD = """---
type: procedure
slug: weekly-digest
title: Weekly digest
trigger: Sunday evening
kind: report
needs:
  exo: [saves, verdicts]
  external: [todo-list]
abort_when:
  - "this week's digest does not exist — say so, do not assemble one"
revised: 2026-08-19
verified: 2026-08-19
serve: true
---

1. Call `saves` for the week just ending.
2. Write five sentences at most.
"""


def write(tmp_path, name: str, text: str):
    """One procedure on disk, with the loader pointed at its directory."""
    d = tmp_path / "procedures"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return d


@pytest.fixture
def at(monkeypatch, tmp_path):
    def _at(dirpath):
        monkeypatch.setattr(config, "PROCEDURES", dirpath)
    return _at


# ─────────────────────────────── the happy shape ───────────────────────────────


def test_a_procedure_projects_with_its_fields_intact(tmp_path, at):
    at(write(tmp_path, "weekly-digest.md", GOOD))
    (row,) = procedure.load()
    assert row.tier == "t1" and row.zone == "procedure"
    assert row.author == "human" and row.grounds is True and row.regenerable is False
    p = row.payload
    assert p["slug"] == "weekly-digest" and p["kind"] == "report"
    assert p["trigger"] == "Sunday evening"
    assert p["serve"] is True
    assert json.loads(p["needs"])["exo"] == ["saves", "verdicts"]
    assert json.loads(p["abort_when"])[0].startswith("this week's digest")
    assert "Call `saves`" in p["body"]


def test_the_id_survives_a_revision(tmp_path, at):
    """Keyed on the slug, not on the payload.

    A procedure is revised often and its serve flag is the most likely edit of
    all. An id hashed from the payload would re-mint on every revision, and the
    surface announces re-minted ids as recently added — a method followed for a
    year would read as new every time a step was reworded.
    """
    at(write(tmp_path, "weekly-digest.md", GOOD))
    before = procedure.load()[0].id
    at(write(tmp_path, "weekly-digest.md",
             GOOD.replace("serve: true", "serve: false").replace("five", "four")))
    assert procedure.load()[0].id == before


def test_no_directory_is_not_an_error(tmp_path, at):
    """A fresh instance has written none, and the engine must never create the
    directory — a directory the engine made is one the engine could fill."""
    at(tmp_path / "nothing-here")
    assert procedure.load() == []
    assert not (tmp_path / "nothing-here").exists()


def test_a_readme_in_the_directory_is_not_a_procedure(tmp_path, at):
    d = write(tmp_path, "weekly-digest.md", GOOD)
    (d / "README.md").write_text("# What lives here\n", encoding="utf-8")
    at(d)
    assert [r.payload["slug"] for r in procedure.load()] == ["weekly-digest"]


# ─────────────────────────── validation fails the build ───────────────────────────


@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t.replace("serve: true\n", ""), "serve"),
    (lambda t: t.replace("trigger: Sunday evening\n", ""), "trigger"),
    (lambda t: t.replace("serve: true", 'serve: "yes"'), "true or false"),
    (lambda t: t.replace("kind: report", "kind: reminder"), "unknown kind"),
    (lambda t: t.replace("type: procedure", "type: note"), "type"),
    (lambda t: t.replace("slug: weekly-digest", "slug: Weekly Digest"), "a-z0-9"),
    (lambda t: t.replace("abort_when:\n", "abort_when: nope\n"), "abort_when"),
    (lambda t: t.replace("needs:\n  exo: [saves, verdicts]\n  external: [todo-list]\n",
                         "needs: saves\n"), "`needs` must be a mapping"),
    (lambda t: t.replace("---\ntype:", "type:", 1), "frontmatter"),
])
def test_a_malformed_procedure_stops_the_build(tmp_path, at, mutate, expect):
    """Fails, never warns. A procedure with a missing field is not a slightly
    worse procedure — it is a document an assistant follows anyway, filling the
    gap with its own guess."""
    at(write(tmp_path, "weekly-digest.md", mutate(GOOD)))
    with pytest.raises(SystemExit) as e:
        procedure.load()
    assert expect in str(e.value)


def test_serve_has_no_default(tmp_path, at):
    """The same rule serve-manifest.json applies to a zone: an undeclared thing
    fails rather than guessing, so nothing publishes by being forgotten."""
    at(write(tmp_path, "weekly-digest.md", GOOD.replace("serve: true\n", "")))
    with pytest.raises(SystemExit):
        procedure.load()


def test_two_files_may_not_claim_one_slug(tmp_path, at):
    d = write(tmp_path, "weekly-digest.md", GOOD)
    (d / "copy.md").write_text(GOOD, encoding="utf-8")
    at(d)
    with pytest.raises(SystemExit) as e:
        procedure.load()
    assert "already claimed" in str(e.value)


def test_the_size_ceiling_forces_decomposition(tmp_path, at):
    """`resources/read` never passes through the worker's cap(), so this is the
    only ceiling that exists on what the zone hands out."""
    fat = GOOD + ("\nAnd then do the thing carefully and completely. " * 400)
    at(write(tmp_path, "weekly-digest.md", fat))
    with pytest.raises(SystemExit) as e:
        procedure.load()
    assert "8,192-byte ceiling" in str(e.value)
    # …and one just under it is fine, so the guard is a ceiling rather than a ban
    # on procedures with any detail in them.
    at(write(tmp_path, "weekly-digest.md", GOOD + ("\nA step. " * 200)))
    assert len(procedure.load()) == 1


# ─────────────────────────────── the acting rule ───────────────────────────────

ACTING = GOOD.replace("kind: report", "kind: action") + ""
ACTING = ACTING.replace(
    "revised: 2026-08-19",
    'acts:\n  - sink: todo-list\n    target: "Reading"\n    reversible: true\n'
    '    dedupe: "reading-night-{month}"\nrevised: 2026-08-19')


def test_an_acting_procedure_must_name_its_sink(tmp_path, at):
    at(write(tmp_path, "act.md", GOOD.replace("kind: report", "kind: action")))
    with pytest.raises(SystemExit) as e:
        procedure.load()
    assert "no `acts:` block" in str(e.value)


def test_a_reporting_procedure_may_not_carry_acts(tmp_path, at):
    at(write(tmp_path, "act.md", ACTING.replace("kind: action", "kind: report")))
    with pytest.raises(SystemExit) as e:
        procedure.load()
    assert "acts on nothing" in str(e.value)


def test_an_authored_target_passes(tmp_path, at):
    at(write(tmp_path, "act.md", ACTING))
    (row,) = procedure.load()
    assert json.loads(row.payload["acts"])[0]["target"] == "Reading"


def test_a_target_may_not_be_computed_from_a_row(tmp_path, at):
    """The acting rule: authored text picks the verb and the target, record text
    may only fill the payload. A target carrying `{person}` when nothing declared
    it is where an injected string in a save chooses where an act lands."""
    at(write(tmp_path, "act.md", ACTING.replace('target: "Reading"',
                                                'target: "{person} list"')))
    with pytest.raises(SystemExit) as e:
        procedure.load()
    assert "authored, never computed" in str(e.value)


def test_a_placeholder_declared_in_dedupe_is_allowed(tmp_path, at):
    at(write(tmp_path, "act.md", ACTING.replace('target: "Reading"',
                                                'target: "Reading {month}"')))
    assert len(procedure.load()) == 1


# ────────────────────────── the serve decision is not its own ──────────────────────────

MANIFEST = {"zones": {"t0_raindrop": "serve", "t1_verdicts": "serve",
                      "t1_notes": "hold", "t2_note_vec": "hold"}}


def _row(slug="d", needs=("saves",), serve=True):
    return {"slug": slug, "serve": serve, "needs": json.dumps({"exo": list(needs)})}


def test_a_served_procedure_over_served_zones_is_fine():
    assert publish._procedure_problems([_row(needs=("saves", "verdicts"))], MANIFEST) == []


def test_a_served_procedure_reading_a_held_zone_fails_the_build():
    """The leak is the map, not the rows. A procedure that says "call `notes_on`
    for what he thinks about X" states that this record holds it, and the tool
    returning nothing does not unsay that."""
    problems = publish._procedure_problems([_row(needs=("notes_on",))], MANIFEST)
    # One per held zone behind the tool: t1_notes and the vectors built from it.
    assert len(problems) == 2
    assert any("t1_notes" in p for p in problems) and all("serve:true" in p for p in problems)


def test_a_held_procedure_may_read_anything():
    assert publish._procedure_problems(
        [_row(needs=("notes_on",), serve=False)], MANIFEST) == []


def test_an_unknown_tool_in_needs_fails_rather_than_being_skipped():
    """A typo that resolves to no zones would pass this check by naming nothing —
    which publishes exactly the procedure whose dependencies nobody verified."""
    problems = publish._procedure_problems([_row(needs=("notes_onn",))], MANIFEST)
    assert len(problems) == 1 and "not a tool on the surface" in problems[0]


# ─────────────────────────────── the map itself ───────────────────────────────


def test_the_tool_map_covers_exactly_the_tools_the_surface_defines():
    """TOOL_ZONES lives in Python and TOOLS lives in JS, so nothing but this test
    keeps them in step. Without it, a tool added on the worker side is a tool
    every procedure may name freely — `zones_for` would raise UnknownTool and the
    publish would refuse a procedure that was correct all along."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "worker/src/tools.js").read_text()
    body = src.split("export const TOOLS = {", 1)[1]
    defined = set(re.findall(r"^  ([a-z_]+): \{$", body, re.M))
    assert defined, "could not read the tool names out of worker/src/tools.js"
    assert defined == set(toolzones.TOOL_ZONES), (
        "exo/toolzones.py and worker/src/tools.js disagree:\n"
        f"  only in the worker: {sorted(defined - set(toolzones.TOOL_ZONES))}\n"
        f"  only in the map:    {sorted(set(toolzones.TOOL_ZONES) - defined)}")


def test_procedures_are_never_embedded():
    """t2 atomizes and embeds by naming its source zones. A procedure reaching
    t2_atom would put imperative text under `whats_relevant`, which promises the
    owner's prose — so the absence has to be asserted, not assumed."""
    from pathlib import Path
    t2 = (Path(__file__).resolve().parent.parent / "exo/t2.py").read_text()
    assert "procedure" not in t2
