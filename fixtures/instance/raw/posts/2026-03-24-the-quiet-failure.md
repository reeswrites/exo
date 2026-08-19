---
title: The quiet failure
slug: the-quiet-failure
description: The failures worth fearing are the ones where every step reports success and the corpus just gets smaller.
type: post
tags: [systems, reliability]
publish_datetime: 2026-03-24T09:00:00
---

The failures I am afraid of are the ones where every step reports success. A
pipeline that loses an input and rebuilds a smaller corpus is green all the way
down, and nothing about the run looks different except a number nobody was
watching.

The only defence I have found is a guard that knows roughly how big the answer
should be and refuses rather than shrinks. It is a crude check, and it has caught
more real problems than every unit test I have written for the same code. Crude and
early beats elegant and late: by the time an elegant check could have proven the
corpus was wrong, I have already published from it.

The corollary is that absence has to be loud. A source that is missing should look
different from a source that had nothing to say, at every layer, all the way out to
whatever finally reads the numbers. Nearly every system I have taken apart collapses
those two into one silent zero.
