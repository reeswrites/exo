"""T1 collections — what is owned, as distinct from what was consumed.

Ownership is a different signal. 40,561 scrobbles say what was played; 89 records
say what was worth buying and keeping in a room, and two blog posts in the record
are about how that choice gets made. Nothing in the store carried that until now.

T1, author=human, grounds=True: these are the owner's own records, hand-maintained,
the same category as t1_visits. Several carry authored prose outright — every fragrance
has a `my_thoughts`.

One zone with a `kind` rather than four zones: they answer the same question and
the manifest should decide them together.
"""
from __future__ import annotations

import csv
import json

from .. import config
from ..provenance import Row, stable_id


def _json(name: str) -> list[dict]:
    path = config.INVENTORY / name
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    for v in data.values():
        if isinstance(v, list):
            return [d for d in v if isinstance(d, dict)]
    return []


def _plays(v) -> str:
    """Scrobble count for an owned record, as a plain number."""
    if isinstance(v, dict):
        n = v.get("scrobbles")
        return str(n) if isinstance(n, int) else ""
    return str(v) if isinstance(v, int) else ""


def _row(kind: str, key: str, payload: dict) -> Row:
    # Coerce at the boundary. These files are hand-maintained JSON, so a title
    # like 1999 arrives as an int and one numeric value in 186 rows is enough to
    # fail the whole parquet write with a type error that names no column.
    payload = {k: (v if v is None else str(v)) for k, v in payload.items()}
    return Row(
        tier="t1", zone="collection", source="inventory", author="human",
        grounds=True, origin_ref=f"inventory/{kind}",
        created=payload.get("acquired") or None,
        id=stable_id("collection", kind, key),
        payload={"kind": kind, **payload},
    )


def load() -> list[Row]:
    rows: list[Row] = []

    for r in _json("records.json"):
        name = r.get("album_name") or ""
        if not name:
            continue
        rows.append(_row("vinyl", f'{r.get("artist_name","")}|{name}', {
            "title": name,
            "creator": r.get("artist_name", ""),
            "genre": r.get("genre", ""),
            "year": str(r.get("release_date") or ""),
            "acquired": r.get("date_purchased") or None,
            # `listened` is the interesting column: owning a record and having
            # played it are different facts, and both are tracked. It arrives as
            # {"scrobbles": N}; the blanket str() below would render that as the
            # literal "{'scrobbles': 110}", so pull the number out here.
            "plays": _plays(r.get("listened")),
            "url": r.get("anchor", ""),
            # Present in the sheet, absent from the JSON copy Exo used
            # to read: what it cost and where it was bought.
            "cost": r.get("total_cost") or r.get("record_cost") or "",
            "retailer": r.get("retailer_name", ""),
            "location": r.get("location", ""),
        }))

    for g in _json("games.json"):
        name = g.get("name") or ""
        if not name:
            continue
        rows.append(_row("board_game", name, {
            "title": name,
            "form": g.get("type", ""),
            "mechanism": g.get("mechanism", ""),
            "url": g.get("game_information", ""),
        }))

    for f in _json("fragrance.json"):
        name = f.get("name") or ""
        if not name:
            continue
        rows.append(_row("fragrance", f'{f.get("maker","")}|{name}', {
            "title": name,
            "creator": f.get("maker", ""),
            "form": f.get("type", ""),
            "notes": " / ".join(x for x in (
                f.get("top_notes"), f.get("heart_notes"), f.get("base_notes")) if x),
            # authored words, the reason this collection is worth serving at all
            "thoughts": f.get("my_thoughts", ""),
            "url": f.get("profile", ""),
        }))

    dvd = config.latest("dvd-*.csv")
    if dvd:
        with open(dvd, newline="", encoding="utf-8-sig") as fh:
            for rec in csv.DictReader(fh):
                title = rec.get("title") or ""
                if not title:
                    continue
                rows.append(_row("dvd", title, {
                    "title": title,
                    "creator": rec.get("creators", ""),
                    "form": rec.get("item_type", ""),
                    "shelf": rec.get("collection", ""),
                }))

    return rows
