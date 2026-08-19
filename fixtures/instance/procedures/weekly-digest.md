---
type: procedure
slug: weekly-digest
title: Weekly digest
trigger: Sunday evening
kind: report
needs:
  exo: [saves, consumption, verdicts]
abort_when:
  - "the week has fewer than three saves — say the week was quiet, do not pad it"
  - "consumption reports a source more than ten days stale — say which, then stop"
revised: 2026-08-19
verified: 2026-08-19
serve: true
---

1. Call `saves` for the week just ending. Group the links by collection, not by
   date: the collection is the decision, the date is an accident of when the tab
   was closed.
2. Call `consumption` for music, books and films. Report the shape — how much,
   how current — rather than the titles. Titles belong in the next step, and
   only when something was finished.
3. Call `verdicts` and include only what was written this week. A verdict from
   three weeks ago is not news; it was news three weeks ago.
4. Write it as five sentences at most. The digest exists to be read on a phone
   on a Sunday, and one that runs longer than the week did is not read at all.
5. State the gaps by name. A week with no film is a fact about the week; a week
   whose film source stopped exporting is a fact about the machinery, and the
   two read identically unless the digest says which one happened.
