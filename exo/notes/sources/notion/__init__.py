"""Notion — two roads to one source.

    exo ingest-notes notion                          the API   (the default)
    exo ingest-notes notion --from Export-1a2b.zip   an export  (offline)

One adapter rather than two, because they agree on identity. Notion's export
glues each page's id onto its filename, and that id is the same dashless UUID the
API returns, so a page read down either road lands as the same note in the same
place. Switching roads costs nothing; running both is idempotent.

**The API is the default**, and the earlier version of this file had that
backwards. The argument for the export was that an export is a *format* and an
API is a *place* (CONTRIBUTING) — but the engine already ships a Trakt puller
with a full OAuth refresh, a Raindrop loader with a bearer token, and a
collections fetch against a Google service account. Notion is a service anyone
can hold an account with, exactly like those. The rule was never "no
credentials"; it is whether a stranger could hold the input, and a stranger can
hold a Notion account.

The practical half is worse. **Notion has no endpoint that triggers an export.**
It is a human opening the app, clicking Export, waiting for an email and moving a
zip out of Downloads — so the export road can never be a step in a nightly, and
notes are the freshest, most-edited thing in the record. Every other source here
either self-refreshes or reads a file that genuinely has no API behind it.
Notes were about to become the one that ran only when somebody remembered.

The export road stays, because it is the right tool for the two jobs it is
actually good at: a one-time migration off Notion, and reading an archive of a
workspace nobody has access to any more. It also needs no network, which is what
makes it the road the tests exercise end to end.
"""
from __future__ import annotations

from ... import SourceNote

LANDING = "notion"
SOURCE = "notion"


def read(src: str | None = None, seen: dict[str, dict] | None = None) -> list[SourceNote]:
    if src:
        from . import export
        return export.read(src)
    from . import api
    return api.read(seen)
