"""The publicity axis, engine side (ADR-0019).

What is being tested is the difference between silence and garbage. A zone with
no grade is `private` and says nothing about it; a zone with a grade nobody
recognises stops the build. Getting that backwards in either direction is the
whole risk: fail on absence and adding a zone becomes a chore, default on
garbage and a `TODO` becomes a decision nobody made.
"""
from __future__ import annotations

import pytest

from exo.scripts_impl.publish import (
    DEFAULT_EXPOSURE,
    EXPOSURE_GRADES,
    _exposure_problems,
    _resolve_exposure,
)

SERVED = ["t0_book", "t0_music", "t1_notes", "t1_post"]


def test_the_vocabulary_is_ordered_least_public_first():
    """`least public wins` is the rule at every level (a zone takes its least
    public row, a tool its least public zone), so the order is load-bearing."""
    assert EXPOSURE_GRADES == ("private", "profile", "published")
    assert DEFAULT_EXPOSURE == EXPOSURE_GRADES[0]


def test_silence_is_private_and_is_not_an_error():
    """A manifest that failed on every ungraded zone would make adding a zone a
    chore rather than a decision."""
    assert _exposure_problems({}, SERVED) == []
    assert _resolve_exposure({}, SERVED) == {z: "private" for z in SERVED}


def test_every_served_zone_is_listed_explicitly():
    """The remote end must never choose a default of its own: a default chosen
    in two places is a default that will eventually differ."""
    resolved = _resolve_exposure({"zone_exposure": {"t1_post": "published"}}, SERVED)
    assert set(resolved) == set(SERVED)
    assert resolved["t1_post"] == "published"
    assert resolved["t1_notes"] == "private"


def test_an_unrecognised_grade_stops_the_build():
    """Somebody typed it, and the one thing it certainly is not is a considered
    `private`. This is what keeps a TODO loud."""
    problems = _exposure_problems(
        {"zone_exposure": {"t0_music": "TODO — profile if Last.fm is public"}}, SERVED)
    assert len(problems) == 1
    assert "not grades" in problems[0] and "t0_music" in problems[0]


def test_public_is_not_a_grade():
    """The vocabulary is closed on purpose. `public` is the word everyone reaches
    for and it is exactly the ambiguity the three grades exist to split."""
    assert _exposure_problems({"zone_exposure": {"t1_post": "public"}}, SERVED)


def test_grading_a_held_zone_is_an_error_not_a_no_op():
    """It is the shape of somebody believing they published something. The zone
    has no rows on the surface to be public about."""
    problems = _exposure_problems({"zone_exposure": {"cache_maps": "published"}}, SERVED)
    assert len(problems) == 1
    assert "not served" in problems[0] and "cache_maps" in problems[0]


def test_a_scoped_run_does_not_report_the_zones_it_skipped_as_unserved():
    """`--only` narrows what a run RECOMPUTES, never what the manifest serves.

    The zones a lane skips are carried forward from the live projection and are
    still on the surface with rows to be public about. Validating grades against
    the narrowed list turned the whole publicity block into a landmine: the
    nightly notes lane rebuilds four zones, and every other graded zone in the
    manifest came back as "graded but not served" — so an instance that filled
    the block in refused to publish on every scoped run and passed on the full
    one, which is the worst way for this to be wrong.
    """
    manifest = {"zone_exposure": {z: "private" for z in SERVED}}
    scoped = ["t1_notes"]
    assert _exposure_problems(manifest, SERVED) == []
    assert _exposure_problems(manifest, scoped), (
        "the narrowed list is what the bug looked like — kept here so the call "
        "site passing the manifest's full served list is the thing under test")


def test_doc_keys_are_prose_not_zones():
    """`_doc` carries the instructions for the humans reading the manifest, and
    it must not be mistaken for a zone graded `[...]`."""
    assert _exposure_problems({"zone_exposure": {"_doc": ["a note to a reader"]}}, SERVED) == []


@pytest.mark.parametrize("grade", EXPOSURE_GRADES)
def test_every_grade_in_the_vocabulary_is_accepted(grade):
    assert _exposure_problems({"zone_exposure": {"t1_post": grade}}, SERVED) == []


def test_the_template_grades_only_zones_it_serves():
    """The shipped example must pass its own validator, or the first thing a
    stranger does is hit a refusal that is the engine's fault rather than theirs."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    m = json.loads((root / "exo" / "templates" / "serve-manifest.json").read_text())
    served = [z for z, d in m["zones"].items() if d == "serve"]
    assert _exposure_problems(m, served) == []
