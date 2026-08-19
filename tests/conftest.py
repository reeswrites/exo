"""Which tests need a built record, and what happens when there isn't one.

Most of the suite is about the engine and needs no data at all: provenance,
the atomizer's cutting rules, the item spine, the project scanner (which builds
throwaway git repos of its own). Six tests are different — they ask questions of
a DuckDB catalog, and a catalog only exists after something has been ingested.

Rather than let those six fail with an IO error on a fresh checkout, they are
marked `record` and skipped when `$EXO_HOME` holds no catalog. Skipped, not
deleted: point EXO_HOME at `fixtures/instance`, run `exo rebuild`, and they run.
The skip message says exactly that, because a silent skip is how a test stops
being run at all.
"""
from __future__ import annotations

import pytest

from exo import config as exo_config


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "record: needs a built record (a DuckDB catalog) to run")


def pytest_collection_modifyitems(items):
    if exo_config.CATALOG.exists():
        return
    skip = pytest.mark.skip(reason=(
        f"no catalog at {exo_config.CATALOG} — set EXO_HOME to a built instance "
        "(e.g. EXO_HOME=fixtures/instance uv run exo rebuild) to run this"))
    for item in items:
        if "record" in item.keywords:
            item.add_marker(skip)
