"""cli_rookery_session — `rookery session` and `rookery identity` commands.

Register with: cli_rookery_session.add_parser(subparsers)
Contract: specs/001-rookery-init-turn-block/contracts/cli-and-hooks.md
Exit codes: 0 ok, 2 bad input or not found, 3 no current session, 4 database busy.
"""

import json
import os
import sqlite3
import sys

EXIT_BAD, EXIT_NO_SESSION, EXIT_BUSY = 2, 3, 4
NO_SESSION_MSG = (
    "No current Rookery session. Set ROOKERY_SESSION or start in a pane "
    "with a session binding."
)


def _die(msg, code=EXIT_BAD):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _row(r):
    return dict(r) if r is not None else None


def _out(args, obj, human):
    if getattr(args, "json", False):
        print(json.dumps(obj, default=str))
    else:
        print(human)


def _open():
    import turn_block  # lazy: written in T008

    return turn_block.open_db()


def _run(args, fn):
    """Open the DB, call fn(conn), map errors to the contract's exit codes."""
    try:
        conn = _open()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "locked" in msg or "busy" in msg:
            _die(f"database is busy: {e}", EXIT_BUSY)
        import turn_block
        _die(f"cannot open database at {turn_block.db_path()}: {e}", EXIT_BAD)
    try:
        return fn(conn)
    except ValueError as e:
        _die(str(e))
    except sqlite3.IntegrityError as e:
        if "bindings" in str(e):
            _die(f"duplicate binding: {e}")
        _die(f"name already exists: {e}")
    except sqlite3.OperationalError as e:
        _die(f"database error: {e}", EXIT_BUSY)
    finally:
        conn.close()


def _need_session(conn, ref):
    s = _row(__import__("rookery_identity").get_session(conn, ref))
    if not s:
        _die(f'No Rookery session "{ref}" exists. List them with: rookery session list')
    return s


def _env_session(conn):
    import rookery_identity as ri

    ref = os.environ.get("ROOKERY_SESSION")
    if ref:
        return _need_session(conn, ref)
    s = _row(ri.find_session(conn, os.environ, ri.load_config(persist=False)))
    if not s:
        _die(NO_SESSION_MSG, EXIT_NO_SESSION)
    return s


# --------------------------------------------------------------- session


def cmd_session_new(args):
    import rookery_identity as ri

    binds = []
    for b in args.bind or []:
        kind, sep, value = b.partition(":")
        if not sep or not kind or not value:
            _die(f'--bind "{b}" is not KIND:VALUE. Example: --bind herdr-workspace:w1')
        binds.append((kind, value))

    def go(conn):
        old = _need_session(conn, args.from_session) if args.from_session else None
        if old is None:
            s = _row(ri.new_session(conn, name=args.name, binds=tuple(binds)))
            copied, failed = [], []
            n_carried = 0
        else:
            import turn_block
            try:
                root = turn_block.project_root(os.path.basename(os.getcwd()))
            except ValueError:
                _die("Cannot find the project folder to copy HANDOFF.md files into: the "
                     "current folder name is not a safe project name. Set ROOKERY_ROOT or "
                     "run from the project folder. No session was created.")
            try:
                srow, made, copied, failed = ri.new_session_from(
                    conn, old["session_id"], args.name, tuple(binds), root)
            except (ValueError, sqlite3.Error):
                raise                    # _run maps these (busy -> 4, else 2)
            except RuntimeError as e:
                _die(f"Could not carry over from {old['name']}: {e} No session was created.")
            s = _row(srow)
            n_carried = len(made)
        out = {"session_id": s["session_id"], "name": s["name"], "copied": copied}
        if old:
            out["failed"] = failed
        human = f"Rookery session {s['name']} ({s['session_id']}) started."
        if old:
            human += (f"\nCarried over {n_carried} agent name(s) from {old['name']}; "
                      f"copied {len(copied)} HANDOFF.md file(s).")
            if failed:
                human += (f"\n{len(failed)} HANDOFF.md copy(ies) failed; the session and "
                          "its agent names were still created (exit 0).")
        _out(args, out, human)
        for f in failed:
            print(f"warning: could not copy HANDOFF.md for {f['name']} "
                  f"(task {f['task']}): {f['reason']}", file=sys.stderr)

    _run(args, go)


def _fmt_session(s):
    return f"{s['session_id']}  {s['name']}  {s['state']}"


def cmd_session_list(args):
    import rookery_identity as ri

    def go(conn):
        if getattr(args, "session", None):
            s = _need_session(conn, args.session)
            ids = [_row(i) for i in ri.list_identities(conn, s["session_id"])]
            out = {"session": s, "identities": ids}
            lines = [_fmt_session(s)] + [f"  {i['agent_id']}  {i['name']}" for i in ids]
            _out(args, out, "\n".join(lines))
            return
        rows = [_row(r) for r in ri.list_sessions(conn)]
        _out(args, {"sessions": rows},
             "\n".join(_fmt_session(s) for s in rows) or "No Rookery sessions.")

    _run(args, go)


def cmd_session_end(args):
    import rookery_identity as ri

    def go(conn):
        _need_session(conn, args.session)
        s = _row(ri.end_session(conn, args.session))
        _out(args, {"session_id": s["session_id"], "name": s["name"], "state": s["state"]},
             f"Rookery session {s['name']} ({s['session_id']}) ended.")

    _run(args, go)


# -------------------------------------------------------------- identity


def cmd_identity_new(args):
    import rookery_identity as ri

    def go(conn):
        s = _need_session(conn, args.session) if args.session else _env_session(conn)
        i = _row(ri.new_identity(conn, s["session_id"], args.name))
        _out(args, {"agent_id": i["agent_id"], "name": i["name"],
                    "session_id": s["session_id"], "session": s["name"]},
             f"Identity {i['name']} ({i['agent_id']}) created in session {s['name']}.")

    _run(args, go)


def cmd_identity_list(args):
    import rookery_identity as ri

    def go(conn):
        s = _need_session(conn, args.session) if args.session else _env_session(conn)
        ids = [_row(i) for i in ri.list_identities(conn, s["session_id"])]
        _out(args, {"session_id": s["session_id"], "session": s["name"], "identities": ids},
             "\n".join(f"{i['agent_id']}  {i['name']}" for i in ids) or "No identities.")

    _run(args, go)


def _env_identity(conn):
    """Identity named by ROOKERY_SESSION + ROOKERY_NODE (full resolve is D20, T0xx)."""
    import rookery_identity as ri

    s = _env_session(conn)
    node = os.environ.get("ROOKERY_NODE")
    if not node:
        _die("No current identity. Set ROOKERY_NODE or run: rookery identity new NAME",
             EXIT_NO_SESSION)
    i = _row(ri.get_identity(conn, node, session_id=s["session_id"]))
    if not i:
        _die(f'No identity "{node}" in session {s["name"]}. '
             "Create it with: rookery identity new NAME")
    return s, i


def cmd_identity_show(args):
    def go(conn):
        s, i = _env_identity(conn)
        _out(args, {"agent_id": i["agent_id"], "name": i["name"],
                    "previous_id": i.get("previous_id"),
                    "session_id": s["session_id"], "session": s["name"]},
             f"You are {i['name']} ({i['agent_id']}) in Rookery session {s['name']}.")

    _run(args, go)


def cmd_identity_bind(args):
    import rookery_identity as ri

    kind, sep, value = args.binding.partition(":")
    if not sep or not kind or not value:
        _die(f'"{args.binding}" is not KIND:VALUE. Example: herdr-pane:p3')

    def go(conn):
        s, i = _env_identity(conn)
        import turn_block

        try:
            root = turn_block.project_root(os.path.basename(os.getcwd()))
        except ValueError:      # cwd name is not a safe project name and no ROOKERY_ROOT
            root = None
        result = ri.bind(conn, s["session_id"], i["agent_id"], kind, value, root=root)
        _out(args, {"agent_id": i["agent_id"], "kind": kind, "value": value, "result": result},
             f"{result.capitalize()} {kind}:{value} to {i['name']} ({i['agent_id']}).")

    _run(args, go)


def cmd_identity_link(args):
    import rookery_identity as ri

    def go(conn):
        s, i = _env_identity(conn)
        r = ri.link_previous(conn, i["agent_id"], args.previous)
        _out(args, {"agent_id": r["agent_id"], "name": r["name"],
                    "previous_id": r["previous_id"], "session_id": s["session_id"]},
             f"{r['name']} ({r['agent_id']}) now follows {r['previous_id']}.")

    _run(args, go)


# ----------------------------------------------------------- inbox / peek


def _mail_root():
    import turn_block

    try:
        return turn_block.project_root(os.path.basename(os.getcwd()))
    except ValueError:
        return None


def _current_identity(conn, root):
    """Resolve like the hooks do (D20), writing nothing. Exits 3 when there is no
    session or no identity."""
    import rookery_identity as ri

    res = ri.resolve(conn, os.environ, {}, root or os.getcwd(), create=False)
    if res is None:
        _die(NO_SESSION_MSG, EXIT_NO_SESSION)
    s, i, bindings = res
    if i is None:
        _die("No current identity. Set ROOKERY_NODE or run: rookery identity new NAME",
             EXIT_NO_SESSION)
    return _row(s), _row(i), bindings


def _unread_with_bodies(conn, agent_id):
    """Unread = recipient is me AND status 'pending' (D7). Sender names come from
    identities.name; the system sender 'rookery' keeps its own name."""
    rows = conn.execute(
        "SELECT m.id, m.sender, COALESCE(i.name, m.sender) AS sender_name, m.topic, "
        "m.body, m.created_at FROM inbox m LEFT JOIN identities i ON i.agent_id = m.sender "
        "WHERE m.recipient=? AND m.status='pending' ORDER BY m.created_at, m.id",
        (agent_id,)).fetchall()
    return [dict(r) for r in rows]


def _mail_text(s, i, msgs):
    if not msgs:
        return f"No unread mail for {i['name']}."
    lines = [f"{len(msgs)} unread for {i['name']} in session {s['name']}:"]
    for m in msgs:
        about = f" about {m['topic']}" if m["topic"] else ""
        lines.append(f"--- #{m['id']} from {m['sender_name']}{about}")
        lines.append(m["body"])
    return "\n".join(lines)


def _mail(args, read):
    import rookery
    import turn_block

    root = _mail_root()

    def go(conn):
        s, i, bindings = _current_identity(conn, root)
        ids = []
        if read:
            # One transaction: select, print, then claim+ack. If the reader has gone away the
            # print fails and nothing changes, so no mail is marked read without being delivered.
            conn.execute("BEGIN IMMEDIATE")
            try:
                msgs = _unread_with_bodies(conn, i["agent_id"])
                ids = [m["id"] for m in msgs]
                obj = {"agent_id": i["agent_id"], "name": i["name"],
                       "session_id": s["session_id"], "session": s["name"],
                       "count": len(msgs), "messages": msgs}
                text = json.dumps(obj, default=str) if getattr(args, "json", False) \
                    else _mail_text(s, i, msgs)
                try:
                    sys.stdout.write(text + "\n")
                    sys.stdout.flush()
                except OSError as exc:      # BrokenPipeError is an OSError
                    try:
                        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
                    except (OSError, ValueError):
                        pass
                    conn.rollback()
                    _die(f"inbox: output closed ({exc}); {len(ids)} unread mail left unread", 1)
                if ids:
                    rookery.claim(conn, ids, commit=False)    # pending -> inflight
                    rookery.ack(conn, ids, commit=False)      # inflight -> done
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            if root:
                turn_block.log_event(root, event="inbox-read", session=s["session_id"],
                                     agent_id=i["agent_id"], name=i["name"],
                                     bindings=[list(b) for b in bindings],
                                     count=len(ids), ids=ids, peek=False)
            return
        else:
            msgs = _unread_with_bodies(conn, i["agent_id"])
            if root:                        # FR-031: peek logs too; it still changes no mail row
                turn_block.log_event(root, event="inbox-read", session=s["session_id"],
                                     agent_id=i["agent_id"], name=i["name"],
                                     bindings=[list(b) for b in bindings],
                                     count=len(msgs), ids=[m["id"] for m in msgs], peek=True)
        _out(args, {"agent_id": i["agent_id"], "name": i["name"],
                    "session_id": s["session_id"], "session": s["name"],
                    "count": len(msgs), "messages": msgs},
             _mail_text(s, i, msgs))

    _run(args, go)


def cmd_inbox(args):
    _mail(args, True)


def cmd_peek(args):
    _mail(args, False)


# ---------------------------------------------------------------- parser


def _json_flag(p):
    p.add_argument("--json", action="store_true", help="Print one JSON object.")


def add_parser(subparsers):
    """Add the `session`, `identity`, `inbox` and `peek` commands to a subparsers action."""
    ps = subparsers.add_parser("session", help="Manage Rookery sessions.")
    ss = ps.add_subparsers(dest="session_cmd", metavar="<action>")
    ss.required = True

    p = ss.add_parser("new", help="Start a Rookery session.")
    p.add_argument("--name", help="Session name (default: the session ID).")
    p.add_argument("--from", dest="from_session", metavar="SESSION",
                   help="Carry over agent names and newest HANDOFF.md from SESSION.")
    p.add_argument("--bind", action="append", metavar="KIND:VALUE",
                   help="Add a session binding, e.g. herdr-workspace:w1. Repeatable.")
    _json_flag(p)
    p.set_defaults(func=cmd_session_new)

    for nm in ("list", "show"):
        p = ss.add_parser(nm, help="List sessions, or show one." if nm == "list"
                          else "Show one session and its identities.")
        p.add_argument("session", nargs="?", metavar="SESSION")
        _json_flag(p)
        p.set_defaults(func=cmd_session_list)

    p = ss.add_parser("end", help="End a session.")
    p.add_argument("session", metavar="SESSION")
    _json_flag(p)
    p.set_defaults(func=cmd_session_end)

    pi = subparsers.add_parser("identity", help="Manage agent identities.")
    si = pi.add_subparsers(dest="identity_cmd", metavar="<action>")
    si.required = True

    p = si.add_parser("new", help="Create an identity in a session.")
    p.add_argument("name", metavar="NAME")
    p.add_argument("--session", metavar="SESSION", help="Default: ROOKERY_SESSION.")
    _json_flag(p)
    p.set_defaults(func=cmd_identity_new)

    p = si.add_parser("list", help="List identities of a session.")
    p.add_argument("--session", metavar="SESSION", help="Default: ROOKERY_SESSION.")
    _json_flag(p)
    p.set_defaults(func=cmd_identity_list)

    p = si.add_parser("show", help="Show who I am (ROOKERY_SESSION + ROOKERY_NODE).")
    _json_flag(p)
    p.set_defaults(func=cmd_identity_show)

    p = si.add_parser("bind", help="Bind KIND:VALUE to my identity.")
    p.add_argument("binding", metavar="KIND:VALUE")
    _json_flag(p)
    p.set_defaults(func=cmd_identity_bind)

    p = si.add_parser("link", help="Set the previous identity of my identity.")
    p.add_argument("--previous", required=True, metavar="AGENT_ID")
    _json_flag(p)
    p.set_defaults(func=cmd_identity_link)

    p = subparsers.add_parser("inbox", help="Read my unread mail and mark it read.")
    _json_flag(p)
    p.set_defaults(func=cmd_inbox)

    p = subparsers.add_parser("peek", help="Show my unread mail without changing anything.")
    _json_flag(p)
    p.set_defaults(func=cmd_peek)
