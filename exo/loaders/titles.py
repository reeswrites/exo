"""One spelling of a title, so two sources can be said to mean the same show.

Trakt and MyAnimeList both hold "Wistoria: Wand and Sword" and neither holds an
id the other knows: Trakt has trakt/imdb/tmdb, MAL has a MAL id, and no export
carries a mapping. The only join key on offer is the title, and the two write it
differently — punctuation, ampersands, romanisation, a stray subtitle.

So the key is the title with everything that varies removed, and the match is
best-effort by construction. That is stated wherever it is used rather than
hidden: `watching` joins on it only to borrow a *date*, and never to borrow an
episode count. A wrong date on one row is a wrong row; a wrong denominator would
be a wrong verdict on how far something got.

Deliberately NOT clever. No season-stripping, no article-stripping, no fuzzy
distance. MAL files each season as its own entry and Trakt keeps one show with
many seasons, so a key that stripped "season 2" would merge two rows that count
different things — which is the one error this must not make. A near miss costs
a fallback; a false match costs the arithmetic.

The value is stored on the row, so it lands in the parquet and in D1 and the
join is a column comparison rather than a function nothing on the SQL side has.
It is NOT part of any id: `tv` and `anime` both pass an explicit `id=`, so this
can be changed without re-minting a life's worth of rows (CONTRIBUTING).
"""
from __future__ import annotations

import re
import unicodedata

_DROP = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


def match_key(title: str) -> str:
    """A title reduced to the part two catalogues agree on.

    Lowercased, accents folded, `&` spelled out, everything else that is not a
    letter, a digit or a space removed, runs of space collapsed. Empty in,
    empty out — and an empty key must never match another empty key, which is
    why every caller guards on `<> ''` rather than trusting the join.
    """
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("&", " and ")
    t = _DROP.sub(" ", t)
    return _SPACE.sub(" ", t).strip()
