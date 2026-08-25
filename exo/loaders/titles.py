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


# A second, COARSER key — and the one place this module does strip a season.
#
# The docstring above forbids that, and it is right about `match_key`: while the
# fraction was computed inside one MAL row, merging "Overlord" with "Overlord
# III" would have compared 13 watched episodes to a 39-episode total and called
# a finished season unfinished.
#
# The flip to Trakt-as-status (ADR-0025) inverts that. The numerator is now
# Trakt's, and Trakt counts a SHOW: 41 episodes of Overlord, not 13 of its first
# season. Against a show-level numerator a per-season denominator is the error —
# 41 of 13 — so the seasons must be summed, and summing them needs a key that
# says two MAL entries are the same show. That is this, and it is why it is a
# separate function: `match_key` still means "the same entry", and nothing that
# borrows a date should start borrowing across seasons.
#
# Still not fuzzy. Only markers that ANNOUNCE themselves as ordinal get removed;
# a bare trailing number does not, or "Mob Psycho 100" becomes "Mob Psycho" and
# a title turns into its own season two.
_SEASON = re.compile(
    r"""\s+(?:
          (?:\d+|1st|2nd|3rd|\d*[04-9]th|11th|12th|13th)\s+season
        | season\s+\d+
        | part\s+\d+
        | cour\s+\d+
        | final\s+season
        | (?:i{2,3}|iv|vi{0,3}|ix|xi{0,3})     # II..XIII, never a bare I
      )$""",
    re.X,
)


def show_key(title: str) -> str:
    """`match_key`, with any trailing season marker peeled off.

    Applied repeatedly, so "Log Horizon 2nd Season Part 2" reduces all the way.
    Empty in, empty out, and the same `<> ''` guard applies at every join.

    This does NOT make the denominator trustworthy on its own. MAL closed in
    August 2023 while Trakt kept counting, so a show whose later seasons were
    never listed rolls up to a total smaller than what Trakt has already
    watched. Summing seasons narrows that gap; it does not close it, and the
    caller is expected to check `watched <= total` before believing the ratio.
    """
    key = match_key(title)
    while True:
        peeled = _SEASON.sub("", key).strip()
        if peeled == key:
            return key
        key = peeled
