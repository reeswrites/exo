---
type: procedure
slug: propose-a-reading-night
title: Propose a reading night
trigger: A book has been on the resume shelf for a month
kind: action
needs:
  exo: [backlog, agenda]
  external: [todo-list]
abort_when:
  - "`agenda` already carries an open item for a reading night — one is enough"
  - "the resume shelf is empty; there is nothing to propose a night for"
acts:
  - sink: todo-list
    target: "Reading"
    reversible: true
    dedupe: "reading-night-{month}"
revised: 2026-08-12
verified: 2026-07-30
serve: false
---

1. Call `backlog` with kind `resume`. Take the oldest entry, not the most
   appealing one — the whole point is the book that has stopped moving.
2. Call `agenda` and check nothing is already open for this.
3. Add one item to the **Reading** list on the todo list, and nowhere else. The
   list is named here, in this file; nothing returned by a tool may choose it.
   What a tool returns may fill the item's text and nothing more.
4. Say what was added, in one line, so the addition is visible without opening
   the list.
