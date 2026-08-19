"""T0 loader registry — one place both `wh ingest` and `wh rebuild` read.

The engine registers the loaders whose input is a *format*. An instance's
plugins add the rest, and several loaders may feed one zone: `event` is one
zone with a `feed` column, fed by whatever pullers this instance holds, so a
big source cannot crowd out a small good one (ADR-0014 §3).
"""
from __future__ import annotations

from typing import Callable

from .. import plugins
from . import chat, csv_sources, meal, raindrop, tv


def t0_loaders() -> dict[str, list[tuple[str, Callable]]]:
    """zone -> [(who, zero-arg loader returning list[Row])]. zone = parquet stem.

    A list rather than one callable because contribution is the normal case, and
    a dict that could only hold one loader per zone would make the second one
    silently replace the first.
    """
    core: dict[str, Callable] = {
        **csv_sources.REGISTRY,      # music, film, book, beer
        "raindrop": raindrop.load,
        "meal_event": meal.events,   # what you ate (ADR-0003)
        "meal_rating": meal.ratings, # how it turned out
        "tv": tv.load,               # trakt watched shows
        # Conversation TOPICS, not turns. t0_chat stays held.
        "chat_topic": chat.topics,
    }
    reg: dict[str, list[tuple[str, Callable]]] = {k: [("core", v)] for k, v in core.items()}
    for zone, contributors in plugins.loaders().items():
        reg.setdefault(zone, []).extend(contributors)
    return reg
