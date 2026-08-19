---
type: chat
origin: chat.example.com
session: sess-0001
title: Publishing a rating scale without recentring it
created: 2026-06-22
thread_url: https://example.com/threads/sess-0001
---

**ada** · 2026-06-22T09:14
I want to publish my restaurant ratings somewhere a stranger can read them, and I have realised the numbers are useless on their own. My median is around eight, so an eight from me is a shrug and every reader is going to take it as praise. What is the minimum extra thing I have to ship alongside?

**assistant** · 2026-06-22T09:15
The usual options are a normalised score, a percentile, or the raw number plus the distribution it came from. Normalising loses the ability to compare you to yourself over time, which sounds like the property you care about.

**ada** · 2026-06-22T09:19
Yes, that is exactly the property. I do not want a rescaled column, I want the grammar published beside the vocabulary. So probably the median and the interquartile range per medium, stated once, and then a rule that nothing quotes a number without it.

**assistant** · 2026-06-22T09:21
That is workable. The failure mode is the calibration going stale while the ratings keep accumulating — it needs to be derived at publish time rather than written down once.

**ada** · 2026-06-22T09:26
Derived at publish time, always, and it should be near the top of whatever context a reader gets rather than buried in a footnote. If someone has to go looking for the scale they will not go looking for the scale.
