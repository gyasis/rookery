"""rookery_wake -- spawn a detached `rookery wake`, take a per-agent lock, find a session's root, read the sweep interval.

Standard library only. The event hooks and the sweep call these helpers (feature 005).
"""
import fcntl
import math
import os
import subprocess
import sys

import turn_block

MIN_SWEEP_S = 300
_children: list = []   # handles of spawned children, polled at each spawn so none stays a zombie


def program_argv() -> list:
    """The command that runs this program: the checkout's rookery_cli.py, or the zip archive."""
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(here):   # inside a zipapp, the dirname is the .pyz file itself
        return [sys.executable, here]
    return [sys.executable, os.path.join(here, "rookery_cli.py")]


def spawn(args, log_root=None) -> bool:
    """Start `rookery <args>` detached and return at once. False when it could not start."""
    try:
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
