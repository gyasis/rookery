"""rookery_guard — the PreToolUse `guard` and PostToolUse `watch` hooks (specs/003-rookery-up, D16, FR-029).

Both are accident guards, not security (002 D24): `guard` stops a Write/Edit outside the agent's own
folders, `watch` flags a Bash command that wrote outside them. Neither raises for a normal input; the
CLI wrapper in cli_hook turns any exception into a log line and exit 0.
"""

import os
import json
import shlex
import socket
import sqlite3
import tempfile

import rookery_identity
import rookery_worktree
import turn_block

_IGNORED = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")
_SEPARATORS = (";", "&&", "||", "|", "&", "|&", "(", ")")


def _root(env, root):
    return root or env.get("ROOKERY_ROOT") or turn_block.gate_root()


def _within(path, folder):
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False


def _identity(conn, env, hook_input, root):
    resolved = rookery_identity.resolve(conn, env, hook_input or {}, root, create=False)
    if resolved is None or resolved[1] is None:
        return None
    return resolved[0], resolved[1]


def allowed_dirs(conn, env, hook_input=None, root=None):
    """(workspace, active worktree path or None) of the identity named by env, or None for no identity."""
    root = _root(env, root)
    found = _identity(conn, env, hook_input, root)
    if found is None:
        return None
    session, ident = found
    ws = os.path.realpath(os.path.join(root, "agents", session["name"], ident["name"]))
    row = conn.execute("SELECT path FROM worktrees WHERE session_id=? AND agent_id=? AND state='active'",
                       (session["session_id"], ident["agent_id"])).fetchone()
    return ws, (os.path.realpath(row[0]) if row else None)


def extra_allowed():
    """Folders the guard always allows: config `guard_allow` (the packaged default when the user config
    lacks the key). `{tmp}` is the temp folder, `~` is the home folder."""
    cfg = rookery_identity.load_config(persist=False)
    entries = cfg.get("guard_allow")
    if entries is None:
        entries = json.loads(rookery_identity._read_default_config()).get("guard_allow") or []
    out = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, str) and e:
            out.append(os.path.realpath(os.path.expanduser(e.replace("{tmp}", tempfile.gettempdir()))))
    return out


def _absolute(path, cwd):
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def _cwd(hook_input):
    cwd = hook_input.get("cwd")
    return cwd if isinstance(cwd, str) and os.path.isabs(cwd) else os.getcwd()


def _other_claim(conn, session, ident, rel):
    rows = conn.execute("SELECT c.pattern, i.name FROM claims c JOIN identities i ON i.agent_id=c.agent_id "
                        "WHERE c.session_id=? AND c.active=1 AND c.agent_id<>? ORDER BY c.pattern, i.name",
                        (session["session_id"], ident["agent_id"])).fetchall()
    for pattern, owner in rows:
        if rookery_worktree.claim_matches(pattern, rel):
            return owner, pattern
    return None


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def guard(hook_input, env, conn, root=None):
    root = _root(env, root)
    found = _identity(conn, env, hook_input, root)
    if found is None:
        return None
    session, ident = found
    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(target, str) or not target:
        return None
    target = _absolute(target, _cwd(hook_input))
    ws, wt = allowed_dirs(conn, env, hook_input, root)
    if _within(target, ws) or any(_within(target, d) for d in extra_allowed()):
        return None
    log = dict(adapter="guard", session=session["session_id"], agent_id=ident["agent_id"],
               name=ident["name"], path=target)
    if wt and _within(target, wt):
        hit = _other_claim(conn, session, ident, os.path.relpath(target, wt))
        if hit is None:
            return None
        turn_block.log_event(root, outcome="claimed", claimed_by=hit[0], pattern=hit[1], **log)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                f"Rookery claims: {hit[0]} owns {hit[1]}. Tell that agent before you change it."}}
    turn_block.log_event(root, outcome="guard-deny", **log)
    return _deny(f"Rookery guard: {target} is outside your folders ({', '.join(d for d in (ws, wt) if d)}). "
                             "Write only in your own folders.")


def _flag(arg):
    return arg.startswith("-") and arg != "-"


def _segment_targets(tokens):
    """Write targets of one simple command (tokens with no separator)."""
    targets, args, i = [], [], 0
    while i < len(tokens):
        t = tokens[i]
        if t in (">", ">>", ">|"):
            if i + 1 < len(tokens):
                targets.append(tokens[i + 1])
            i += 2
            continue
        if t in ("<", "<<", "<<<", ">&", "<&"):
            i += 2
            continue
        args.append(t)
        i += 1
    while args and "=" in args[0] and not args[0].startswith(("=", "-")):
        args.pop(0)                 # leading VAR=value
    if not args:
        return targets
    cmd, rest = os.path.basename(args[0]), [a for a in args[1:] if not _flag(a)]
    if cmd == "tee":
        targets += rest
    elif cmd in ("cp", "mv", "install") and len(rest) >= 2:
        targets.append(rest[-1])
    elif cmd in ("touch", "mkdir"):
        targets += rest
    return targets


def write_targets(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    tokens = list(lex)
    targets, seg = [], []
    for t in tokens + [";"]:
        if t in _SEPARATORS:
            targets += _segment_targets(seg)
            seg = []
        else:
            seg.append(t)
    return targets


def watch(hook_input, env, conn, root=None):
    root = _root(env, root)
    found = _identity(conn, env, hook_input, root)
    if found is None:
        return None
    session, ident = found
    tool_input = hook_input.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        targets = write_targets(command)
    except ValueError:
        return None                 # fail safe: an unparsable command is not flagged
    folders = [d for d in allowed_dirs(conn, env, hook_input, root) if d] + extra_allowed()
    cwd, flagged = _cwd(hook_input), []
    for t in targets:
        if not t or t in _IGNORED:
            continue
        path = _absolute(t, cwd)
        if path in _IGNORED or any(_within(path, d) for d in folders):
            continue
        if path not in flagged:
            flagged.append(path)
    if not flagged:
        return None
    for path in flagged:
        turn_block.log_event(root, adapter="watch", outcome="watch-flag", session=session["session_id"],
                             agent_id=ident["agent_id"], name=ident["name"], path=path)
    text = f"Rookery watch: this command wrote {', '.join(flagged)}, outside your folders."
    return {"systemMessage": text,
            "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


class PortError(Exception):
    """No free port is left in the range. The message is the report text after 'not started: '."""


def _bindable(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def assign_ports(conn, agent_id, config):
    """The agent's {name: port} map (D16, FR-008). Names it already has keep their port. A new name gets the
    lowest port in config["ports"]["range"] that no `ports` row has and that binds on 127.0.0.1. One
    transaction, so two `up` runs cannot take the same port; a UNIQUE clash moves on to the next port."""
    cfg = config.get("ports") or {}
    lo, hi = cfg["range"]
    names = cfg.get("names") or []
    conn.commit()                       # BEGIN needs no transaction open
    conn.execute("BEGIN IMMEDIATE")
    try:
        have = dict(conn.execute("SELECT name, port FROM ports WHERE agent_id=?", (agent_id,)).fetchall())
        taken = {r[0] for r in conn.execute("SELECT port FROM ports")}
        for name in names:
            if name in have:
                continue
            for port in range(lo, hi + 1):
                if port in taken or not _bindable(port):
                    continue
                try:
                    conn.execute("INSERT INTO ports(agent_id, name, port) VALUES(?,?,?)", (agent_id, name, port))
                except sqlite3.IntegrityError:
                    taken.add(port)
                    continue
                taken.add(port)
                have[name] = port
                break
            else:
                raise PortError(f"no free port in {lo}-{hi}")
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return {n: have[n] for n in names if n in have}
