"""rookery_wake -- run one wake under a lock (`rookery wake`), spawn a detached `rookery wake`, take a per-agent lock, find a session's root, read the sweep interval.

Standard library only. The event hooks and the sweep call these helpers (feature 005).
"""
import fcntl
import math
import os
import sqlite3
import subprocess
import sys
import threading
import time

import turn_block

MIN_SWEEP_S = 300
_children: list = []   # handles of spawned children, polled at each spawn so none stays a zombie
_children_lock = threading.Lock()   # the mailroom serves requests on threads


def program_argv() -> list:
    """The command that runs this program: the checkout's rookery_cli.py, or the zip archive."""
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(here):   # inside a zipapp, the dirname is the .pyz file itself
        return [sys.executable, here]
    return [sys.executable, os.path.join(here, "rookery_cli.py")]


def spawn(args, log_root=None) -> bool:
    """Start `rookery <args>` detached and return at once. False when it could not start."""
    try:
        with _children_lock:
            _children[:] = [p for p in _children if p.poll() is None]
            _children.append(subprocess.Popen(
                program_argv() + list(args), start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True))
        return True
    except Exception as exc:
        try:
            turn_block.log_event(log_root, "postmaster", outcome="wake-spawn-failed", reason=str(exc))
        except Exception:
            pass   # logging must never raise
        return False


def try_lock(path, log_root=None):
    """Take an exclusive non-blocking lock on path. The open file, or None when held or on error.
    An error that is not "held" (a folder or file that cannot be opened) logs `lock-failed`."""
    fh = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fh = open(path, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except Exception as exc:
        if fh is not None:
            fh.close()
        if not isinstance(exc, BlockingIOError):
            try:
                turn_block.log_event(log_root, "postmaster", outcome="lock-failed", path=path, reason=str(exc))
            except Exception:
                pass   # logging must never raise
        return None


def resolve_root(conn, session_id):
    """The session's rookery-root binding, else ROOKERY_ROOT, else None."""
    row = conn.execute("SELECT value FROM bindings WHERE session_id=? AND owner_type='session' "
                       "AND kind='rookery-root'", (session_id,)).fetchone()
    if row is not None and row[0]:
        return row[0]
    return os.environ.get("ROOKERY_ROOT") or None


def sweep_interval(config):
    """(seconds, raised). sweep_s defaults to 300 and is never below 300."""
    if not isinstance(config, dict):
        return MIN_SWEEP_S, False
    value = config.get("sweep_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return MIN_SWEEP_S, False
    if not math.isfinite(value):   # json.load accepts NaN and Infinity; time.sleep refuses both
        return MIN_SWEEP_S, True
    if value < MIN_SWEEP_S:
        return MIN_SWEEP_S, True
    return value, False


def _unpushed(conn, root, agent_id):
    """Ids of the pending mail for agent_id that the push gate accepts and pushed/ does not list."""
    import postmaster
    done, last = postmaster._load_pushed(root, agent_id)
    out = []
    for r in conn.execute("SELECT id, sender, topic, body FROM inbox WHERE recipient=? "
                          "AND status='pending' ORDER BY id", (agent_id,)).fetchall():
        listed = r["id"] in done if done is not None else r["id"] <= last
        if not listed and postmaster._needs_push(r, agent_id, conn):
            out.append(r["id"])
    return out


def after_pending(conn, msg_ids, log_root=None) -> int:
    """Spawn one detached `rookery wake` per (session, recipient) that has a pending mail in msg_ids
    that the push gate accepts. Skips a missing row, a non-pending one, `legacy` or no session, a session
    that is not open, a self-send and a mail that _needs_push rejects. Never raises: a failure logs one
    `wake-spawn-failed` line. Returns the number of wakes spawned."""
    try:
        import postmaster
        groups = {}
        for mid in msg_ids:
            row = conn.execute("SELECT id, sender, recipient, topic, body, status, session_id FROM inbox "
                               "WHERE id=?", (mid,)).fetchone()
            if row is None or row["status"] != "pending" or row["sender"] == row["recipient"]:
                continue
            sid = row["session_id"]
            if not sid or sid == "legacy":
                continue
            sess = conn.execute("SELECT state FROM rookery_sessions WHERE session_id=?", (sid,)).fetchone()
            if sess is None or sess["state"] != "open":
                continue
            if not postmaster._needs_push(row, row["recipient"], conn):
                continue
            groups[(sid, row["recipient"])] = True
        spawned = 0
        for sid, agent in groups:
            if spawn(["wake", "--session", sid, "--agent", agent], log_root=log_root):
                spawned += 1
        return spawned
    except Exception as exc:
        try:
            turn_block.log_event(log_root, "postmaster", outcome="wake-spawn-failed", reason=str(exc))
        except Exception:
            pass   # logging must never raise
        return 0


def run_wake(conn, root, config, session_id, agent_id, rounds=3, run=subprocess.run, now=time.time) -> dict:
    """One wake for one identity under ROOT/postmaster/lock/AGENT_ID.lock (research R4, R5).
    Takes the lock for each round, calls postmaster.wake_one, releases, then looks again: another round
    only after `pushed` or `nothing`, only while unpushed mail is there, at most `rounds` rounds.
    A stop outcome (busy, backoff-wait, wake-failed, ...) still gets one more round when mail newer than the
    round saw arrived meanwhile: a second wake that found the lock held left that mail to this one.
    Returns {"session", "agent_id", "outcome", "mail_id", "rounds", "capped"}. `capped` is true when the
    round limit left pushable mail for the sweep."""
    import postmaster
    result = {"session": session_id, "agent_id": agent_id, "outcome": "nothing", "mail_id": None, "rounds": 0,
              "capped": False}
    if root is None:
        turn_block.log_event(None, "postmaster", outcome="no-root", session=session_id, agent_id=agent_id)
        result["outcome"] = "no-root"
        return result
    if not turn_block.is_safe_name(agent_id):
        result["outcome"] = "unsafe-name"
        return result
    lock_path = os.path.join(root, "postmaster", "lock", agent_id + ".lock")
    cap = max(1, rounds)
    more = False
    while result["rounds"] < cap:
        lock = try_lock(lock_path, log_root=root)
        if lock is None:
            if _lock_writable(lock_path):
                turn_block.log_event(root, "postmaster", outcome="wake-busy", session=session_id, agent_id=agent_id)
                result["outcome"] = "wake-busy"
            else:
                result["outcome"] = "lock-failed"   # try_lock logged the reason; nobody holds the lock
            more = False
            break
        seen = max(_unpushed(conn, root, agent_id), default=0)
        try:
            ident = conn.execute(
                "SELECT i.agent_id, i.name, i.session_id, s.name AS sname FROM identities i "
                "JOIN rookery_sessions s ON s.session_id=i.session_id "
                "WHERE i.agent_id=? AND i.session_id=? AND s.state='open'", (agent_id, session_id)).fetchone()
            if ident is None:
                result["outcome"] = "nothing"
                break
            result["rounds"] += 1
            result["outcome"] = postmaster.wake_one(conn, root, config, ident, run=run, backoff=None, now=now)
        finally:
            lock.close()
        ids = _unpushed(conn, root, agent_id)
        more = bool(ids) and (result["outcome"] in ("pushed", "nothing") or max(ids) > seen)
        if not more:
            break
    result["capped"] = more and result["rounds"] >= cap
    ids = _unpushed(conn, root, agent_id)
    if ids:
        result["mail_id"] = ids[-1]
    else:
        row = conn.execute("SELECT MAX(id) AS m FROM inbox WHERE recipient=? AND status='pending'",
                           (agent_id,)).fetchone()
        result["mail_id"] = row["m"] if row else None
    return result


def _lock_writable(path):
    """True when the lock file (or its folder, before the file exists) can be written: a None from
    try_lock then means another holder, not a broken folder."""
    if os.path.exists(path):
        return os.access(path, os.W_OK)
    folder = os.path.dirname(path)
    return os.path.isdir(folder) and os.access(folder, os.W_OK)


EXIT_BAD, EXIT_MISSING, EXIT_BUSY = 2, 3, 4


def _fail(msg, code):
    print(f"rookery: {msg}", file=sys.stderr)
    sys.exit(code)


def cmd_wake(args):
    import json
    import rookery
    import rookery_identity
    for what, val in (("--session", args.session), ("--agent", args.agent)):
        if not isinstance(val, str) or not val or not (turn_block.is_safe_name(val)):
            _fail(f"{what}: {val!r} is not a safe name", EXIT_BAD)
    conn = None
    try:
        conn = rookery.connect(turn_block.db_path())
        sess = rookery_identity.get_session(conn, args.session)
        if sess is None:
            _fail(f"no session {args.session!r}", EXIT_MISSING)
        ident = rookery_identity.get_identity(conn, args.agent, sess["session_id"])
        if ident is None:
            _fail(f"no agent {args.agent!r} in session {args.session!r}", EXIT_MISSING)
        config = rookery_identity.load_config(persist=False)
        result = run_wake(conn, resolve_root(conn, sess["session_id"]), config,
                          sess["session_id"], ident["agent_id"])
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "locked" in msg or "busy" in msg:
            _fail(f"database busy: {exc}", EXIT_BUSY)
        _fail(f"database error: {exc}", 1)
    except SystemExit:
        raise
    except Exception as exc:                 # a bad config, a file error: one line, no traceback
        _fail(f"wake failed: {exc}", 1)
    finally:
        if conn is not None:
            conn.close()
    if args.json:
        print(json.dumps(result))
    else:
        print(f"wake {ident['name']}: {result['outcome']}")


def add_parser(subparsers):
    p = subparsers.add_parser("wake", help="Push a notice to one idle agent that has new mail.")
    p.add_argument("--session", required=True, help="Session name or ID.")
    p.add_argument("--agent", required=True, help="Identity name or ID in that session.")
    p.add_argument("--json", action="store_true", help="Print the result as JSON.")
    p.set_defaults(func=cmd_wake)
