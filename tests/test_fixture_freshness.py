"""The fixture must keep exercising the windowed code paths.

Two published sections read only the last 90 days — conversation topics, and
the workshop's recent activity. The fixture's material is dated, so both drain
as it ages, and when they reach empty nothing fails: the projection simply
stops carrying two sections and the suite still passes. A fixture that has
quietly stopped testing something is worse than no fixture, because it reads
like coverage.

So the ageing is asserted rather than waited for. `fixtures/refresh-dates.py`
slides every date forward by a whole number of days, preserving the intervals
between them, and this fails once that is due.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "fixtures" / "refresh-dates.py"


def test_the_fixture_has_not_aged_out_of_its_own_windows():
    if not SCRIPT.exists():
        return  # an instance that is not this repo has no fixture to check
    proc = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
