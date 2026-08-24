-- The second door (ADR-0021) makes "by whom" ambiguous unless the log says
-- which door opened. ADR-0010 extended the caller log to answer by-whom; this
-- keeps that answer true now that there are two ways in.
--
-- RUN THIS BEFORE DEPLOYING the Worker version that writes `door`. The audit
-- writer is wrapped in a try/catch so it can never take the surface down, which
-- also means a column mismatch fails SILENTLY — the surface keeps answering and
-- simply stops logging. Order matters more than usual here.
--
--   npx wrangler d1 execute warehouse --remote --file migrations/0002-which-door.sql
--
-- Not idempotent: ALTER TABLE ADD COLUMN errors if the column exists. Re-running
-- is expected to fail on the ALTER and that failure is harmless.

ALTER TABLE wh_audit ADD COLUMN door TEXT;

-- wh_callers carries the door inside `outcome` instead of in a column of its
-- own, and that is not laziness. `outcome` is part of the primary key, so a new
-- column would have to join the key to keep the per-day rollup correct — which
-- means rebuilding the table rather than altering it. The values are:
--
--   ok                 answered through the header door
--   ok:oauth           answered through a grant at /mcp
--   denied             refused at either door — no valid credential
--   denied:authorize   a wrong token typed into the consent screen
--
-- The last one is new and is the one worth watching: it is the only event here
-- that means somebody was guessing rather than misconfigured.
