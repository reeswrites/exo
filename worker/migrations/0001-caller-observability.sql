-- Phase 1 of origin gating (ADR-0007): learn where calls actually come from
-- before writing an allowlist that could lock out the client it protects.
--
-- RUN THIS BEFORE DEPLOYING the Worker version that writes ip/asn. The audit
-- writer is wrapped in a try/catch so it can never take the surface down, which
-- also means a column mismatch fails SILENTLY — the surface keeps answering and
-- simply stops logging. Order matters more than usual here.
--
--   npx wrangler d1 execute warehouse --remote --file migrations/0001-caller-observability.sql
--
-- Not idempotent: ALTER TABLE ADD COLUMN errors if the column exists. Re-running
-- a second time is expected to fail on the ALTERs and that failure is harmless.

ALTER TABLE wh_audit ADD COLUMN ip TEXT;
ALTER TABLE wh_audit ADD COLUMN asn INTEGER;

-- The Worker creates this itself on first call; created here too so the table
-- can be queried before any traffic arrives.
CREATE TABLE IF NOT EXISTS wh_callers (
  day        TEXT,
  ip         TEXT,
  asn        INTEGER,
  org        TEXT,
  country    TEXT,
  colo       TEXT,
  ua         TEXT,
  outcome    TEXT,     -- 'ok' | 'denied'
  n          INTEGER,
  first_seen TEXT,
  last_seen  TEXT,
  PRIMARY KEY (day, ip, asn, ua, outcome)
);
