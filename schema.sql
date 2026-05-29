-- Rookery — the "mailroom": durable system-of-record for the agent mesh.
-- This DB IS the audit log. Everything is plain SQL, queryable at any time.

CREATE TABLE IF NOT EXISTS nodes (
  node_id   TEXT PRIMARY KEY,
  kind      TEXT,            -- headless | daemon | terminal
  status    TEXT,            -- idle | busy | waiting | asleep | offline
  lifecycle TEXT,            -- ephemeral (no memory) | persistent (rehydrates from DB)
  session_ref TEXT,          -- LLM session id for persistent agents (claude --resume)
  pid       INTEGER,         -- OS pid (for the pause.sh / resume.sh "video button")
  last_seen INTEGER          -- heartbeat epoch
);

CREATE TABLE IF NOT EXISTS inbox (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  sender       TEXT NOT NULL,
  recipient    TEXT NOT NULL,
  body         TEXT NOT NULL,
  topic        TEXT,
  delivered    INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL,
  delivered_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inbox_undelivered ON inbox(recipient, delivered);

-- The CIBA "phone-home" path. token_ref is a POINTER to a secret (e.g. a
-- keychain item / JIT broker handle) — the real secret is NEVER stored here.
CREATE TABLE IF NOT EXISTS credential_requests (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id    TEXT NOT NULL,
  resource   TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | denied
  token_ref  TEXT,
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);
