#!/usr/bin/env python3
"""Rookery — shared library for the agent-mesh mailroom.

The mailroom is a single SQLite DB that acts as the durable system-of-record
AND the audit log. Every helper here is small and stdlib-only so any node
(headless Claude, raw-API daemon, or a human at a terminal) can read/write the
same bus.
"""
import os
import shutil
import sqlite3
import subprocess
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ROOKERY_DB", os.path.join(_HERE, "rookery.db"))
_SCHEMA_PATH = os.path.join(_HERE, "schema.sql")


def now() -> int:
    return int(time.time())


def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")     # concurrent readers + 1 writer
    conn.execute("PRAGMA busy_timeout=5000;")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with open(_SCHEMA_PATH) as fh:
        conn.executescript(fh.read())
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


def set_session_ref(conn, node_id, session_ref):
    conn.execute(
        "UPDATE nodes SET session_ref=? WHERE node_id=?", (session_ref, node_id)
    )
    conn.commit()


# --- mail ------------------------------------------------------------------
def send(conn, sender, recipient, body, topic=None):
    cur = conn.execute(
        "INSERT INTO inbox(sender, recipient, body, topic, delivered, created_at) "
        "VALUES(?,?,?,?,0,?)",
        (sender, recipient, body, topic, now()),
    )
    conn.commit()
    return cur.lastrowid


def fetch_undelivered(conn, recipient):
    return conn.execute(
        "SELECT * FROM inbox WHERE recipient=? AND delivered=0 ORDER BY created_at, id",
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


def claim(conn, ids):
    """Mark messages delivered the instant we pick them up (claim pattern) so
    the poller never re-injects the same mail. Trade-off: a crash mid-handling
    drops the claimed mail — acceptable for v1, noted in the README."""
    conn.executemany(
        "UPDATE inbox SET delivered=1, delivered_at=? WHERE id=?",
        [(now(), i) for i in ids],
    )
    conn.commit()


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
