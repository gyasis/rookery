#!/usr/bin/env python3
"""Rookery — shared library for the agent-mesh mailroom.

The mailroom is a single SQLite DB that acts as the durable system-of-record
AND the audit log. Every helper here is small and stdlib-only so any node
(headless Claude, raw-API daemon, or a human at a terminal) can read/write the
same bus.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = os.path.join(_HERE, "schema.sql")
_USER_DB = os.path.join(os.path.expanduser("~"), ".rookery", "rookery.db")


def _default_db_path() -> str:
    """Where the mailroom lives when ROOKERY_DB is unset.

    A source checkout keeps the DB beside the code (unchanged behaviour). A
    zipapp install cannot: _HERE points *inside* the archive, so sqlite3 fails
    with "unable to open database file". Fall back to ~/.rookery/rookery.db
    whenever _HERE is not a writable directory.
    """
    local = os.path.join(_HERE, "rookery.db")
    if os.path.exists(local):
        return local                      # existing checkout — never relocate it
    if os.path.isdir(_HERE) and os.access(_HERE, os.W_OK):
        return local
    os.makedirs(os.path.dirname(_USER_DB), exist_ok=True)
    return _USER_DB


DB_PATH = os.environ.get("ROOKERY_DB") or _default_db_path()


def now() -> int:
    return int(time.time())


def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")     # concurrent readers + 1 writer
    conn.execute("PRAGMA busy_timeout=5000;")
    init_db(conn)
    return conn


def _load_schema() -> str:
    """Read schema.sql from disk (repo) or, when running inside a zipapp where
    open() can't reach a path inside the archive, via the module loader."""
    try:
        with open(_SCHEMA_PATH) as fh:
            return fh.read()
    except OSError:
        loader = globals().get("__loader__")
        if loader is not None and hasattr(loader, "get_data"):
            # zipimport wants the path under loader.archive, which may be relative (./rookery)
            return loader.get_data(os.path.join(getattr(loader, "archive", _HERE),
                                                "schema.sql")).decode("utf-8")
        raise


# Read the schema ONCE, at import. Under a zipapp _load_schema() reaches into
# the archive via the module loader, so a lazy per-connection read means a
# long-lived process (notably `rookery serve`) dies with
# "ZipImportError: bad local file header" the moment the installed pyz is
# rebuilt underneath it. Reading at startup makes an upgrade cost the running
# server nothing.
try:
    _SCHEMA_SQL = _load_schema()
except Exception:                      # tolerate an odd import context
    _SCHEMA_SQL = None


_INBOX_SCREENING_COLS = ("verdict TEXT", "reason TEXT", "decided_by TEXT", "decided_at INTEGER")


# D18: Rookery sessions, session-scoped identities and bindings (additive only).
_SESSION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS rookery_sessions (
  session_id TEXT PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  state      TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  ended_at   TEXT
);
CREATE TABLE IF NOT EXISTS identities (
  agent_id    TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  name        TEXT NOT NULL,
  previous_id TEXT,
  created_at  TEXT NOT NULL,
  UNIQUE(session_id, name)
);
CREATE TABLE IF NOT EXISTS bindings (
  session_id TEXT NOT NULL,
  owner_type TEXT NOT NULL,
  owner_id   TEXT NOT NULL,
  kind       TEXT NOT NULL,
  value      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, owner_type, kind, value)
);
-- Loop breaker (FR-039): mails sent on one task between two agents since the last human prompt.
-- a <= b, so both directions share a row; a = b is a self-send.
CREATE TABLE IF NOT EXISTS loop_counts (
  session_id TEXT NOT NULL,
  task       TEXT NOT NULL,
  a          TEXT NOT NULL,
  b          TEXT NOT NULL,
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(session_id, task, a, b)
);
"""


def _add_column(conn: sqlite3.Connection, alter_sql: str) -> None:
    """Run an ALTER TABLE ADD COLUMN; a concurrent init_db may have added it first."""
    try:
        conn.execute(alter_sql)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def init_db(conn: sqlite3.Connection) -> None:
    global _SCHEMA_SQL
    if _SCHEMA_SQL is None:
        _SCHEMA_SQL = _load_schema()
    conn.executescript(_SCHEMA_SQL)
    # CREATE TABLE IF NOT EXISTS won't add columns to an existing DB — migrate.
    have = {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}
    for col in _INBOX_SCREENING_COLS:
        if col.split()[0] not in have:
            _add_column(conn, f"ALTER TABLE inbox ADD COLUMN {col}")
    conn.executescript(_SESSION_TABLES_SQL)
    if "session_id" not in have:
        _add_column(conn, "ALTER TABLE inbox ADD COLUMN session_id TEXT DEFAULT 'legacy'")
    conn.execute(
        "INSERT OR IGNORE INTO rookery_sessions(session_id, name, state, created_at) "
        "VALUES('legacy', 'legacy', 'open', strftime('%Y-%m-%dT%H:%M:%SZ','now'))"
    )
    conn.commit()


# --- nodes -----------------------------------------------------------------
def register_node(conn, node_id, kind="headless", pid=None, lifecycle="ephemeral"):
    conn.execute(
        "INSERT INTO nodes(node_id, kind, status, lifecycle, pid, last_seen) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(node_id) DO UPDATE SET "
        "kind=excluded.kind, lifecycle=excluded.lifecycle, pid=excluded.pid, "
        "last_seen=excluded.last_seen",
        (node_id, kind, "idle", lifecycle, pid, now()),
    )
    conn.commit()


def set_status(conn, node_id, status):
    conn.execute(
        "UPDATE nodes SET status=?, last_seen=? WHERE node_id=?",
        (status, now(), node_id),
    )
    conn.commit()


def heartbeat(conn, node_id):
    conn.execute("UPDATE nodes SET last_seen=? WHERE node_id=?", (now(), node_id))
    conn.commit()


def get_node(conn, node_id):
    return conn.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()


def get_address(conn, node_id):
    """A node's pane/endpoint address (nodes.address), or None.

    Whatever put it there — terminal_node, a plugin binding panes to nodes, a
    remote peer — this is how the rest of the mesh asks "where does this node
    live?" without knowing which mechanism answered.
    """
    row = conn.execute(
        "SELECT address FROM nodes WHERE node_id=?", (node_id,)
    ).fetchone()
    return row["address"] if row and row["address"] else None


def set_address(conn, node_id, address):
    conn.execute(
        "UPDATE nodes SET address=?, last_seen=? WHERE node_id=?",
        (address, now(), node_id),
    )
    conn.commit()


def set_session_ref(conn, node_id, session_ref):
    conn.execute(
        "UPDATE nodes SET session_ref=? WHERE node_id=?", (session_ref, node_id)
    )
    conn.commit()


# --- mail ------------------------------------------------------------------
# Mail from the mesh's own plumbing is never held — a bounce or a wake notice
# stuck behind the screener it reports on would deadlock the loop.
SYSTEM_SENDERS = {"postmaster", "relay", "rookery"}


def hold_enabled() -> bool:
    """ROOKERY_HOLD=1 turns on comms screening: agent mail lands 'held' until a
    screener releases or quarantines it. Off by default (mail goes straight to
    'pending', as before)."""
    return os.environ.get("ROOKERY_HOLD", "").lower() in ("1", "true", "yes", "on")


def send(conn, sender, recipient, body, topic=None, hold=None, *, session_id,
         commit=True):
    """The one low-level insert. `session_id` is required and never defaults to `legacy`
    (FR-005): only mail_gate and rookery itself call this."""
    if not session_id:
        raise ValueError("rookery.send needs a session_id")
    if hold is None:
        hold = hold_enabled() and sender not in SYSTEM_SENDERS
    cur = conn.execute(
        "INSERT INTO inbox(sender, recipient, body, topic, status, delivered, created_at, session_id) "
        "VALUES(?,?,?,?,?,0,?,?)",
        (sender, recipient, body, topic, "held" if hold else "pending", now(),
         session_id),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def count_loop(conn, session_id, keys) -> bool:
    """Loop breaker counters (FR-039). keys = [(task, a, b, limit), ...]; a and b are put in order
    here. Runs inside the caller's transaction and never commits. If any counter is at its limit
    the result is True and nothing moves. Otherwise every counter goes up by one and the result is
    False."""
    keys = [(t, *sorted((a, b)), lim) for t, a, b, lim in keys]
    for t, a, b, lim in keys:
        row = conn.execute("SELECT count FROM loop_counts WHERE session_id=? AND task=? AND a=? AND b=?",
                           (session_id, t, a, b)).fetchone()
        if row is not None and row[0] >= lim:
            return True
    for t, a, b, _lim in keys:
        conn.execute(
            "INSERT INTO loop_counts(session_id, task, a, b, count) VALUES(?,?,?,?,1) "
            "ON CONFLICT(session_id, task, a, b) DO UPDATE SET count=count+1",
            (session_id, t, a, b))
    return False


def fetch_undelivered(conn, recipient):
    return conn.execute(
        "SELECT * FROM inbox WHERE recipient=? AND status='pending' ORDER BY created_at, id",
        (recipient,),
    ).fetchall()


def fetch_thread(conn, node):
    """Full conversation involving this node (sent OR received), oldest first.
    A persistent node's rehydrated memory — context lives in the DB, not the
    process, so a killed-and-re-upped partner resumes with full history."""
    return conn.execute(
        "SELECT * FROM inbox WHERE sender=? OR recipient=? ORDER BY created_at, id",
        (node, node),
    ).fetchall()


def claim(conn, ids, commit=True):
    """Mark messages IN-FLIGHT when a node picks them up, so the poller won't
    re-inject them while they're being processed. If the node crashes before
    ack(), requeue_stale() returns them to 'pending' (no lost mail)."""
    conn.executemany(
        "UPDATE inbox SET status='inflight', claimed_at=? WHERE id=?",
        [(now(), i) for i in ids],
    )
    if commit:
        conn.commit()


def ack(conn, ids, commit=True):
    """Mark in-flight messages DONE after the turn handled them successfully."""
    conn.executemany(
        "UPDATE inbox SET status='done', delivered=1, delivered_at=? WHERE id=?",
        [(now(), i) for i in ids],
    )
    if commit:
        conn.commit()


def dlq(conn, msg_id, reason):
    """Move a message to the dead-letter queue (undeliverable) with a reason."""
    conn.execute("UPDATE inbox SET status='dlq', note=? WHERE id=?", (reason, msg_id))
    conn.commit()


def bump_attempt(conn, msg_id):
    conn.execute("UPDATE inbox SET attempts=attempts+1 WHERE id=?", (msg_id,))
    conn.commit()
    return conn.execute("SELECT attempts FROM inbox WHERE id=?", (msg_id,)).fetchone()["attempts"]


def list_dlq(conn):
    return conn.execute("SELECT * FROM inbox WHERE status='dlq' ORDER BY id").fetchall()


def requeue_dlq(conn, msg_id):
    conn.execute(
        "UPDATE inbox SET status='pending', attempts=0, note=NULL WHERE id=? AND status='dlq'",
        (msg_id,),
    )
    conn.commit()


def requeue_stale(conn, dead_after):
    """Return in-flight mail to 'pending' ONLY when the node that claimed it has
    gone silent (genuinely dead) — judged by the recipient node's last_seen, not
    by how long the turn is taking. A node heartbeats WHILE it works, so a slow
    model (e.g. Ollama taking minutes) keeps its claim and is NOT requeued.
    Returns the count requeued."""
    cur = conn.execute(
        "UPDATE inbox SET status='pending', claimed_at=NULL "
        "WHERE status='inflight' AND recipient IN ("
        "  SELECT node_id FROM nodes WHERE last_seen IS NULL OR last_seen < ?"
        ")",
        (now() - dead_after,),
    )
    conn.commit()
    return cur.rowcount


# --- comms screening: held -> pending | quarantined -------------------------
# A screener (human, typed-decision classifier, or comms-monitor agent) decides
# each held message. Quarantine is a labelled folder, not a delete: every row
# keeps (message, verdict, reason, decided_by) as training data, and the sender
# gets a bounce so it can fix its routing instead of waiting forever.
QUARANTINE_REASONS = (
    "wrong-recipient", "task-already-done", "unneeded", "off-team",
    "malformed", "injection-suspect", "loop",
)


def list_held(conn):
    return conn.execute("SELECT * FROM inbox WHERE status='held' ORDER BY created_at, id").fetchall()


def get_message(conn, msg_id):
    return conn.execute("SELECT * FROM inbox WHERE id=?", (msg_id,)).fetchone()


def _decide(conn, msg_id, from_status, to_status, verdict, reason, decided_by,
            commit=True):
    cur = conn.execute(
        "UPDATE inbox SET status=?, verdict=?, reason=?, decided_by=?, decided_at=? "
        "WHERE id=? AND status=?",
        (to_status, verdict, reason, decided_by, now(), msg_id, from_status),
    )
    if commit:
        conn.commit()
    if cur.rowcount != 1:
        row = get_message(conn, msg_id)
        state = row["status"] if row else "missing"
        raise ValueError(f"message #{msg_id} is {state}, expected {from_status}")


def release(conn, msg_id, decided_by):
    """Approve a held message: it becomes 'pending', so the postmaster wakes the recipient."""
    _decide(conn, msg_id, "held", "pending", "release", None, decided_by)


def quarantine(conn, msg_id, reason, decided_by, note=None, suggest=None, bounce=True,
               commit=True, bounce_limit=None):
    """Reject a held message into quarantine and (by default) bounce it to the
    sender with the reason and an optional suggested recipient. Returns the
    bounce message id, or None."""
    if reason not in QUARANTINE_REASONS:
        raise ValueError(f"reason must be one of {QUARANTINE_REASONS}, got {reason!r}")
    _decide(conn, msg_id, "held", "quarantined", "quarantine", reason, decided_by,
            commit=commit)
    if note:
        conn.execute("UPDATE inbox SET note=? WHERE id=?", (note, msg_id))
        if commit:
            conn.commit()
    if not bounce:
        return None
    m = get_message(conn, msg_id)
    body = json.dumps({
        "kind": "bounce", "msg_id": msg_id, "to": m["recipient"], "reason": reason,
        "note": note, "suggest": suggest, "decided_by": decided_by,
    })
    held = bounce_limit is not None and count_loop(
        conn, m["session_id"], [("~bounce", "rookery", m["sender"], bounce_limit)])
    bid = send(conn, "rookery", m["sender"], body, topic=m["topic"],
               session_id=m["session_id"] or "legacy", hold=True if held else None, commit=False)
    if held:                                  # FR-039: a bounce loop is held, not delivered
        conn.execute("UPDATE inbox SET reason='loop', note=? WHERE id=?",
                     ("Held: too many bounces to this agent with no human prompt in between.", bid))
    if commit:
        conn.commit()
    return bid


def unquarantine(conn, msg_id, decided_by):
    """Overrule a quarantine (the screener was wrong): deliver it after all.
    The original reason is kept on the row next to verdict='unquarantine' —
    that pair is a correction label."""
    cur = conn.execute(
        "UPDATE inbox SET status='pending', verdict='unquarantine', decided_by=?, decided_at=? "
        "WHERE id=? AND status='quarantined'",
        (decided_by, now(), msg_id),
    )
    conn.commit()
    if cur.rowcount != 1:
        raise ValueError(f"message #{msg_id} is not quarantined")


def list_quarantined(conn, reason=None):
    if reason:
        return conn.execute(
            "SELECT * FROM inbox WHERE status='quarantined' AND reason=? ORDER BY id", (reason,)
        ).fetchall()
    return conn.execute("SELECT * FROM inbox WHERE status='quarantined' ORDER BY id").fetchall()


def screening_labels(conn):
    """Every screened message with its decision — the training/eval set for the
    screener (release = positive, quarantine = negative, unquarantine = the
    screener's false positive)."""
    return conn.execute(
        "SELECT id, sender, recipient, topic, body, verdict, reason, decided_by, decided_at "
        "FROM inbox WHERE verdict IS NOT NULL ORDER BY decided_at, id"
    ).fetchall()


# --- credentials (the CIBA phone-home) -------------------------------------
def request_credential(conn, node_id, resource):
    cur = conn.execute(
        "INSERT INTO credential_requests(node_id, resource, status, created_at) "
        "VALUES(?,?, 'pending', ?)",
        (node_id, resource, now()),
    )
    conn.commit()
    return cur.lastrowid


def get_credential(conn, req_id):
    return conn.execute(
        "SELECT * FROM credential_requests WHERE id=?", (req_id,)
    ).fetchone()


def pending_credentials(conn):
    return conn.execute(
        "SELECT * FROM credential_requests WHERE status='pending' ORDER BY created_at"
    ).fetchall()


def approve_credential(conn, req_id, token_ref):
    conn.execute(
        "UPDATE credential_requests SET status='approved', token_ref=?, decided_at=? "
        "WHERE id=?",
        (token_ref, now(), req_id),
    )
    conn.commit()


def deny_credential(conn, req_id):
    conn.execute(
        "UPDATE credential_requests SET status='denied', decided_at=? WHERE id=?",
        (now(), req_id),
    )
    conn.commit()


def resolve_secret(token_ref):
    """Resolve a credential POINTER to the real secret at the moment of use.
    The secret is NEVER stored in the DB — only the pointer is. Supports:
      env://VAR                     -> os.environ[VAR]
      keychain://<service>/<account> -> `secret-tool lookup` (libsecret)
    Returns the secret string, or None if it can't be resolved."""
    if not token_ref:
        return None
    if token_ref.startswith("env://"):
        return os.environ.get(token_ref[len("env://"):])
    if token_ref.startswith("keychain://"):
        service, _, account = token_ref[len("keychain://"):].partition("/")
        if shutil.which("secret-tool") and account:
            try:
                r = subprocess.run(
                    ["secret-tool", "lookup", "service", service, "account", account],
                    capture_output=True, text=True, timeout=10,
                )
                return r.stdout or None
            except Exception:
                return None
    return None
