# ADR-0025 — The live source sets status; the closed one sets scale

Status: accepted · 2026-08-25

Supersedes the status half of [ADR-0024](0024-a-denominator-is-a-source.md).

## Context

ADR-0024 gave television a denominator by making MyAnimeList a source rather
than a calculation. It was right about the arithmetic and wrong about one thing
it never checked: whether the list was still being kept.

It is not. The export in `raw/` is dated 2023-08-31 and there will not be a
newer one — the owner stopped using MyAnimeList and moved to Trakt. Everything
uncomfortable about `watching` follows from treating a closed archive as a live
one:

- **Four shows were reported as `watching`.** Not four shows being watched —
  four rows whose `my_status` said "Watching" on the day the list was abandoned.
  The surface asserted them in the present tense for three years, and would have
  gone on asserting them.
- **`stalled` could never fire.** It needs a date. Every date input was empty:
  this export format carries no `my_last_updated` at all (the loader mapped a
  field that is not in the file, writing 243 empty strings), `my_start_date` is
  `0000-00-00` on all 243 rows, and `my_finish_date` is a sentinel on 200 of
  them — so the only surviving date belonged to titles that were *finished*, and
  a finished show does not stall. The one status the tool derived rather than
  read was unreachable by construction.
- **The list gatekept the population.** Rows came from `t0_anime`, so a show
  watched after August 2023 could not appear however many episodes Trakt held.
  Naruto, 109 episodes, was invisible.

The join was built to borrow a date off Trakt and nothing else. With the list
closed, the date is the only part of the answer that was still true.

## Decision

**Trakt is the spine and the sole source of status. MyAnimeList supplies scale
and history, and never a present-tense claim.**

- `watching` selects from `t0_tv`. The population is every show watched, not the
  243 that reached a list. `status` is derived here — `watching`, `stalled`,
  `completed`, `unknown` — from Trakt's episode count and Trakt's date.
- MAL's word survives as `declared`, always beside `declared_on: 2023-08-31`.
  It is a fact about a list, and it is stamped with the day that list stopped so
  it cannot be read as a fact about now.
- Asking `status='dropped'` is answered by routing to `declared`, with a note
  saying so. It was a status in the old schema; a caller using it should get the
  seven dropped shows, not an empty list that reads as "he dropped nothing".

### The denominator needs two keys, not one

Trakt counts a **show**; MAL files a **season** each. `match_key` was
deliberately not clever about this — merging "Overlord" with "Overlord III"
would have compared a 13-episode season to a 39-episode total while the fraction
was computed inside one MAL row.

Inverting the spine inverts that. The numerator is Trakt's and spans the show,
so a per-season denominator is now the error: 41 of 13. `show_key` is therefore
a second, coarser key that peels a trailing season marker, and the totals of
every entry sharing one are summed. `match_key` is unchanged and still means
"the same entry" — the exact row that `declared` and `score` are read off.

Only markers that announce themselves as ordinal are peeled. A bare trailing
number is not one, or "Mob Psycho 100" becomes its own season two.

### A total that cannot be true is not served

Summing seasons narrows the gap. It does not close it, and this is the part
worth being explicit about: **the archive's coverage is frozen too.** A show the
owner kept watching past his last list edit rolls up to a total smaller than
what Trakt has already counted — Dr. Stone, 80 episodes watched against a listed
24; High School DxD, 48 against 12. Of 44 show-level matches, 10 are short.

So the guard is `watched <= total`. A rolled-up total already behind the watch
count is not a denominator; it is a number that stopped being one. It is
withheld, and `no_episode_total` says which of the three reasons applies —
never on the list, the list stops short, or the list carries no total (MAL
writes 0 while a series airs). A ratio built on it would have read as finished
and then some.

## Consequences

- `watching` covers 358 shows instead of 243, and most of them have no
  denominator. That was always true of the television record and ADR-0023
  already requires a slice to say what it is a slice of; the tool now reports
  how many of its rows can be given a total.
- `stalled` works. Twelve shows, where the old query could return none — BLUE
  LOCK at 20 of 24, untouched since April 2023.
- The anime shelf keeps its whole remaining value. Episode totals do not rot: a
  twelve-episode series stays twelve however long ago it was listed. What rots
  is coverage and status, and neither is now trusted.
- A newer export, if one ever appears, improves coverage and changes nothing
  structural. Nothing here assumes the list is dead — only that it is not
  authoritative about the present.
- The `my_last_updated` mapping is left in place and still writes empty strings
  for this format. It is dead weight for the 2023 export and correct for a
  format that carries the field; the loader should probably say when a mapped
  field is absent from every row, which is not this change.
