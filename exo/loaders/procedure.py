"""t1_procedure — how the owner does a recurring thing, in their own words.

Every other zone in this store is descriptive: it says what was played, written,
rated, committed. This one is imperative. A procedure is the owner's own method
for something that recurs — the weekly digest, the way a hangout gets proposed —
written by hand, and published as an MCP *resource* rather than as a tool
(ADR-0016). A tool is a named question whose answer depends on the record; a
procedure is a document with a stable identity and content that does not vary
with the record, which is the tools/resources line exactly.

That inversion is the whole security story of this file. Text an assistant is
meant to ACT on may only ever have one author:

  * hand-written only. No loader path into it, no promotion from `captures/`,
    nothing derived. `author="human"` is asserted, not passed in.
  * never embedded. `t2` atomizes and embeds notes and posts by name; procedures
    are absent from both passes on purpose, so `whats_relevant` cannot answer a
    question about the owner's thinking with a list of instructions.

The rest is validation, and it fails the build rather than warning. A procedure
with a missing field is not a slightly worse procedure — it is a document an
assistant will follow anyway, filling the gap with its own guess.

The 8 KB ceiling is the one that looks arbitrary and is not. `resources/read`
never passes through the worker's `cap()`, so nothing downstream bounds the size
of what this zone hands out; this is the only ceiling that exists. It also does
the thing a length limit rarely does — failing forces decomposition, and a
decomposed procedure is a followable one.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .. import config, io
from ..provenance import Row, stable_id

# Documentation of the directory, not a method. An instance is expected to keep
# a README here saying what the shape is; it is not a procedure.
NOT_PROCEDURES = {"README.md", "CONTRIBUTING.md"}

# What `resources/read` will match. The worker's URI regex is the same one; a
# slug that fails here would publish a resource nothing could ever address.
SLUG = re.compile(r"^[a-z0-9-]+$")

# Two values, and it stays two. `kind` answers one question — does following
# this reach outside the record — and everything else that distinguishes one
# procedure from another is already expressible in `needs` and `abort_when`. A
# growing taxonomy would need per-type handling in the loader, in publish, and
# in the renderer, which is three places to forget.
KINDS = {"report", "action"}

REQUIRED = ("slug", "title", "trigger", "kind", "revised", "serve")

# The serialised row ceiling. See the module docstring: `resources/read` has no
# other bound.
MAX_ROW_BYTES = 8 * 1024

# `{placeholder}` in an `acts` target.
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


class ProcedureError(SystemExit):
    """A procedure that must not be built. Raised, never printed and skipped.

    SystemExit rather than a bare exception because the honest response to an
    unloadable procedure is to stop the build with a legible message — the same
    treatment `publish --cf` gives a value that cannot survive the trip to D1.
    """


def _fail(path: Path, msg: str) -> None:
    raise ProcedureError(f"procedures: {path.name}: {msg}")


def _frontmatter(path: Path, text: str) -> tuple[dict, str]:
    """Parse the YAML block, and FAIL on anything malformed.

    `t1_index` has a parser of the same shape that swallows a YAMLError and
    projects the note anyway, which is right for a vault: a half-typed value in
    one note must not be able to fail an ingest of three thousand. It is wrong
    here. An unparseable procedure has no `serve` decision, and the fail-closed
    rule cannot be applied to a field that did not parse.
    """
    if not text.startswith("---"):
        _fail(path, "no frontmatter block")
    end = text.find("\n---", 3)
    if end == -1:
        _fail(path, "frontmatter block is never closed")
    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as exc:
        _fail(path, f"frontmatter is not valid YAML — {str(exc).splitlines()[0]}")
    if not isinstance(fm, dict):
        _fail(path, "frontmatter is not a mapping")
    return fm, text[end + 4:].lstrip("\n")


def _check_acts(path: Path, acts: list) -> None:
    """An acting procedure names its sink and its target, and the AUTHOR names them.

    The rule this enforces is the acting rule (CONTEXT.md): authored text picks
    the verb and the target, record text may only fill the payload. A target
    carrying `{person}` when nothing declared `{person}` is a target computed
    from a row — which is the shape where an injected string in a save or a note
    chooses where an act lands.
    """
    if not isinstance(acts, list) or not acts:
        _fail(path, "kind: action with no `acts:` block — an act must name its sink")
    for i, act in enumerate(acts):
        where = f"acts[{i}]"
        if not isinstance(act, dict):
            _fail(path, f"{where} is not a mapping")
        for field in ("sink", "target"):
            if not str(act.get(field, "")).strip():
                _fail(path, f"{where} has no `{field}`")
        dedupe = str(act.get("dedupe", ""))
        declared = set(PLACEHOLDER.findall(dedupe))
        used = set(PLACEHOLDER.findall(str(act["target"])))
        undeclared = sorted(used - declared)
        if undeclared:
            _fail(path, f"{where} target computes {', '.join('{%s}' % p for p in undeclared)} "
                        "from a row — a target is authored, never computed")
        if "reversible" in act and not isinstance(act["reversible"], bool):
            _fail(path, f"{where} `reversible` must be true or false")


def load() -> list[Row]:
    src = config.PROCEDURES
    if not src.exists():
        # Normal, and not worth a warning: a fresh instance has written none, and
        # the engine must never create this directory — the files are the
        # instance's, and a directory the engine made is a directory the engine
        # could fill.
        return []
    rows: list[Row] = []
    seen: dict[str, str] = {}
    for path in io.markdown(src, exclude=NOT_PROCEDURES):
        fm, body = _frontmatter(path, path.read_text(encoding="utf-8", errors="replace"))

        missing = [f for f in REQUIRED if f not in fm]
        if missing:
            # `serve` is in that list and has no default, deliberately. An
            # undeclared procedure fails the build rather than guessing, the
            # same rule serve-manifest.json applies to a zone.
            _fail(path, "missing required field(s): " + ", ".join(missing))
        if str(fm.get("type", "")).strip() != "procedure":
            _fail(path, "frontmatter `type` must be `procedure`")
        if not isinstance(fm["serve"], bool):
            _fail(path, "`serve` must be true or false, not a string")
        if fm["kind"] not in KINDS:
            _fail(path, f"unknown kind {fm['kind']!r} — one of {', '.join(sorted(KINDS))}")

        slug = str(fm["slug"])
        if not SLUG.match(slug):
            _fail(path, f"slug {slug!r} is not [a-z0-9-]+ — the resource URI could not address it")
        if slug in seen:
            # Two files, one URI. `resources/read` would return whichever the
            # query happened to pick, which is a coin flip over which method the
            # assistant follows.
            _fail(path, f"slug {slug!r} is already claimed by {seen[slug]}")
        seen[slug] = path.name

        needs = fm.get("needs") or {}
        if not isinstance(needs, dict):
            _fail(path, "`needs` must be a mapping with `exo:` and/or `external:` lists")
        for axis in ("exo", "external"):
            if axis in needs and not isinstance(needs[axis], list):
                _fail(path, f"`needs.{axis}` must be a list")
        abort_when = fm.get("abort_when") or []
        if not isinstance(abort_when, list):
            _fail(path, "`abort_when` must be a list of conditions")
        acts = fm.get("acts") or []
        if fm["kind"] == "action":
            _check_acts(path, acts)
        elif acts:
            _fail(path, "kind: report carries an `acts:` block — a reporting procedure acts on nothing")

        row = Row(
            tier="t1", zone="procedure", source="procedures", author="human",
            created=str(fm["revised"]), origin_ref=path.name,
            # Keyed on the slug, like drafts and recipes. The id must survive an
            # edit: a procedure is revised often, and flipping `serve` is the
            # most likely edit of all. An id hashed from the payload would
            # re-mint on every revision and announce a method the owner has been
            # following for a year as recently added.
            id=stable_id("procedures", slug),
            payload={
                "slug": slug,
                "title": str(fm["title"]),
                "trigger": str(fm["trigger"]),
                "kind": str(fm["kind"]),
                # D1 has no list type, so these travel as JSON strings and the
                # worker parses them on the way out — same as t1_recipe.
                "needs": json.dumps(needs, sort_keys=True),
                "abort_when": json.dumps(abort_when),
                "acts": json.dumps(acts, sort_keys=True),
                "verified": str(fm.get("verified", "")),
                "serve": bool(fm["serve"]),
                "body": body.strip(),
            },
        )
        size = len(json.dumps(row.flat(), default=str).encode("utf-8"))
        if size > MAX_ROW_BYTES:
            _fail(path, f"serialised row is {size:,} bytes, over the {MAX_ROW_BYTES:,}-byte "
                        "ceiling — `resources/read` has no other bound, so split this into "
                        "procedures that each do one thing")
        rows.append(row)
    return rows
