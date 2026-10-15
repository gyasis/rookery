"""cli_hook — `rookery hook session-start|prompt|stop --project NAME`.

Register with: cli_hook.add_parser(subparsers)
Contract: specs/001-rookery-init-turn-block/contracts/cli-and-hooks.md
Every hook exits 0 and prints nothing to stdout in this module's current scope
(D16, FR-017). `session-start` and `prompt` are empty until T037 / T038.
"""

import datetime
import json
import os
import secrets
import sqlite3
import sys


class HookArgError(Exception):
    """Raised instead of argparse's exit(2): a hook must always exit 0 silently."""


def _raise_arg_error(message):
    raise HookArgError(message)


def _no_session_possible(turn_block, rookery_identity):
    """Cheap pre-check, touches no disk: True when no session reference is in the
    environment, or the database does not exist (a hook never creates it)."""
    if not os.environ.get("ROOKERY_SESSION"):
        sources = rookery_identity.load_config(persist=False).get("session_binding_sources") or {}
        if not any(os.environ.get(var) for var in sources):
            return True
    return not os.path.exists(turn_block.db_path())


def _read_input():
    """Parse stdin as a JSON object. Returns (dict, None) or ({}, reason)."""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        return {}, f"stdin unreadable: {exc}"
    if not raw.strip():
        return {}, "empty stdin"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, f"stdin is not JSON: {exc}"
    if not isinstance(data, dict):
        return {}, "stdin JSON is not an object"
    return data, None


def stop(args):
    """Stop hook (FR-005..009, FR-017, D1, D16). Never blocks, never prints."""
    root = None
    text = None
    try:
        import turn_block
        import rookery_identity

        if _no_session_possible(turn_block, rookery_identity):
            return                          # no session: print nothing, write nothing
        root = turn_block.project_root(args.project)
        hook_input, bad = _read_input()
        text = hook_input.get("last_assistant_message")
        if bad:
            turn_block.log_event(root, adapter="stop", outcome="bad-input", reason=bad)
            return                          # FR-022: exactly one outcome line per call
        conn = turn_block.open_db()
        try:
            resolved = rookery_identity.resolve(
                conn, os.environ, hook_input, root, create=False)
            if resolved is None:
                return                      # no session: print nothing, write nothing
            session, identity, _bindings = resolved
            text = hook_input.get("last_assistant_message")
            if not isinstance(text, str):
                turn_block.log_event(
                    root, adapter="stop", session=session["session_id"],
                    outcome="no-message-field",
                    reason="Stop JSON has no last_assistant_message")
                return
            if identity is None:
                _keep_unresolved(turn_block, root, session, text)
                return
            ctx = {"root": root, "session": session, "identity": identity,
                   "adapter": "stop"}
            turn_block.dispatch_text(conn, text, ctx)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        busy = "locked" in msg or "busy" in msg
        if busy and root is not None and isinstance(text, str):
            try:     # D16: a valid block is not lost; identity is unknown here
                import turn_block
                _keep_unresolved(turn_block, root, None, text, outcome="db-busy",
                                 reason=str(exc))
                return
            except Exception:
                pass
        _safe_log(root, outcome="db-busy" if busy else "hook-error", reason=str(exc))
    except Exception as exc:                # D16: log and exit 0
        _safe_log(root, outcome="hook-error", reason=f"{type(exc).__name__}: {exc}")


def _safe_log(root, **fields):
    if root is None:
        return
    try:
        import turn_block
        turn_block.log_event(root, adapter="stop", **fields)
    except Exception:
        pass


def _keep_unresolved(turn_block, root, session, text, outcome="no-identity",
                     reason="session has no identity for this caller"):
    """Session but no identity: keep a VALID block in <root>/unresolved/ and log
    `no-identity`. Writes the file only when turn_block.find_last_block and
    turn_block.validate both exist (T016/T017) and validation passes; else only logs."""
    sid = session["session_id"] if session else None
    find = getattr(turn_block, "find_last_block", None)
    validate = getattr(turn_block, "validate", None)
    block = None
    if find is not None and validate is not None:
        raw = find(text)
        if raw is not None:
            block, _note = validate(raw, root)
    if block is not None and not turn_block.is_safe_name(block.get("task")):
        block = None                        # unsafe task would escape unresolved/
    if block is not None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = os.path.join(root, "unresolved", f"{block['task']}-{stamp}.json")
        if os.path.exists(path):
            path = os.path.join(root, "unresolved",
                                f"{block['task']}-{stamp}-{secrets.token_hex(3)}.json")
        turn_block.write_json_atomic(path, block)
    turn_block.log_event(root, adapter="stop", session=sid, outcome=outcome,
                         reason=reason,
                         task=(block or {}).get("task"), kept=block is not None)


def session_start(args):
    """Empty until T037."""


def prompt(args):
    """Empty until T038."""


def add_parser(sub):
    p = sub.add_parser("hook", help="Claude Code hook entry points (silent, exit 0).")
    p.error = _raise_arg_error
    hs = p.add_subparsers(dest="hook_command", metavar="<hook>")
    hs.required = True
    for name, fn in (("session-start", session_start), ("prompt", prompt), ("stop", stop)):
        hp = hs.add_parser(name, help=f"{name} hook")
        hp.error = _raise_arg_error
        hp.add_argument("--project", required=True, help="Rookery project name")
        hp.set_defaults(func=fn)
