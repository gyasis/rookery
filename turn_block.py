"""Rookery turn block — shared helpers (research D6, D15, D16).

Paths, safe names, structured logging, database access and atomic JSON writes
used by the turn-block hooks, `rookery turn` and the postmaster.
"""
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import tempfile
import time

import rookery

SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")

_LOG_FILES = {
    "turn-block": os.path.join("logs", "turn-block.jsonl"),
    "postmaster": os.path.join("postmaster", "postmaster.jsonl"),
}
_LOGGERS: dict = {}   # log path -> Logger, one handler per file


def is_safe_name(name) -> bool:
    """True for 1-64 chars of [A-Za-z0-9._-], excluding '.' and '..'."""
    return (isinstance(name, str) and SAFE_NAME.fullmatch(name) is not None
            and name not in (".", ".."))


def _check_name(kind: str, name) -> str:
    if not is_safe_name(name):
        raise ValueError(f"unsafe {kind} name: {name!r}")
    return name


def db_path() -> str:
    """ROOKERY_DB, else ~/Documents/code/rookery/rookery.db (D6)."""
    return os.environ.get("ROOKERY_DB") or os.path.expanduser(
        "~/Documents/code/rookery/rookery.db")


def project_root(project: str) -> str:
    """ROOKERY_ROOT, else ~/.rookery/projects/<project>/ (D6)."""
    env = os.environ.get("ROOKERY_ROOT")
    if env:
        return env
    return os.path.join(os.path.expanduser("~/.rookery/projects"), _check_name("project", project))


def workspace(root: str, session_name: str, agent_name: str) -> str:
    """<root>/agents/<session>/<name>/, created on demand."""
    path = os.path.join(root, "agents", _check_name("session", session_name),
                        _check_name("agent", agent_name))
    os.makedirs(path, exist_ok=True)
    return path


def _logger_for(path: str) -> logging.Logger:
    lg = _LOGGERS.get(path)
    if lg is not None and not os.path.exists(path):
        # file deleted externally: the handler writes to an unlinked inode
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        lg = None
    if lg is None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lg = logging.getLogger("rookery.turn_block." + path)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
        _LOGGERS[path] = lg
    return lg


def log_event(root: str, stream: str = "turn-block", **fields) -> None:
    """Append one JSON line (ts + fields) to the stream's rotated log (D15).

    Fields by convention: session, agent_id, name, task, adapter, outcome,
    reason, msg_id. Logging never raises.
    """
    try:
        rel = _LOG_FILES[stream]
        rec = {"ts": rookery.now()}
        rec.update(fields)
        _logger_for(os.path.join(root, rel)).info(json.dumps(rec, default=str))
    except Exception:
        pass


def _schema_current(conn: sqlite3.Connection) -> bool:
    """True when the sessions schema and the legacy session row already exist
    (checked with reads only, so it works while another connection writes)."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"rookery_sessions", "identities", "bindings", "inbox"} <= have:
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}
    if "session_id" not in cols or not {c.split()[0] for c in rookery._INBOX_SCREENING_COLS} <= cols:
        return False
    return conn.execute(
        "SELECT 1 FROM rookery_sessions WHERE session_id='legacy'").fetchone() is not None


def open_db() -> sqlite3.Connection:
    """Hook-grade connection: 2 s lock wait, WAL, Row factory, schema ensured (D16)."""
    conn = sqlite3.connect(db_path(), timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=2000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    if not _schema_current(conn):     # read-only callers must not need the write lock
        rookery.init_db(conn)
    return conn


def write_json_atomic(path: str, obj) -> None:
    """Write obj as JSON via a temp file in the same folder, then os.replace."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
