"""Raindrop save-time parsing accepts both formats it arrives in.

The live API returns ISO; the MCP snapshot stores RFC. A regression here silently
drops every `created` on a full API sync (see loaders/raindrop.py docstring), which
makes the zone dateless and breaks windowing (raindrop-recap).
"""
from __future__ import annotations

from exo.loaders.raindrop import _to_iso


def test_iso_api_format_is_kept():
    assert _to_iso("2024-01-15T10:00:00.000Z") == "2024-01-15T10:00:00+00:00"
    assert _to_iso("2024-01-15T10:00:00Z") == "2024-01-15T10:00:00+00:00"


def test_rfc_snapshot_format_is_parsed():
    assert _to_iso("Sat, 08 Aug 2026 18:33:39 GMT") == "2026-08-08T18:33:39+00:00"


def test_empty_and_junk_are_none():
    assert _to_iso("") is None
    assert _to_iso("not a date") is None
    assert _to_iso(None) is None  # type: ignore[arg-type]
