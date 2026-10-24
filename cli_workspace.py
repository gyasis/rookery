"""cli_workspace — `rookery up TEAM_FILE`: build a team (specs/003-rookery-up).

Register with: cli_workspace.add_parser(subparsers)
Order of work: research.md D22. This module does steps 1, 2, 3, 5 and 7 (no launch, no worktree yet).
Exit codes: 0 ok, 2 bad input, 4 database busy (as cli_rookery_session).
Never writes ~/.claude.json, ~/.claude/settings.json or any file in the host repo.
"""

import json
import os
import sqlite3
import subprocess
import sys

EXIT_BAD, EXIT_BUSY = 2, 4
FALLBACK_BINDING = "herdr-pane"
ROOT_BINDING = "rookery-root"
URL_MSG = "`rookery up` now builds a team. To join a peer, use `rookery join URL`."


class _Bad(Exception):
    def __init__(self, msg, code=EXIT_BAD):
        super().__init__(msg)
        self.code = code


def _git_out(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise _Bad("Rookery needs git to find the host repo. Install git, then run the command again.")
    return r.stdout.strip() if r.returncode == 0 else None


def host_repo(cwd):
    """Return (host repo path, project name) for cwd (D5). Linked worktrees map to the main worktree."""
    top = _git_out(cwd, "rev-parse", "--show-toplevel")
    if not top:
        raise _Bad("This folder is not in a git repository. Run `rookery up` inside the repo you work on.")
    top = os.path.realpath(top)
    common = _git_out(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return top, os.path.basename(top)
    common = os.path.realpath(common)
    base = os.path.basename(common)
    if base == ".git":
        host = os.path.dirname(common)
        return host, os.path.basename(host)
    name = base[:-4] if base.endswith(".git") and len(base) > 4 else base
    return top, name


def _resolve_root(host, project):
    import turn_block
    env_root = os.environ.get("ROOKERY_ROOT")
    if env_root and not os.path.isabs(env_root):
        raise _Bad(f"ROOKERY_ROOT must be an absolute path, not {env_root!r}. Set a full path.")
    try:
        root = os.path.abspath(turn_block.project_root(project))
    except ValueError:
        raise _Bad(f"The folder name {project} is not a safe project name. "
                   "Set ROOKERY_ROOT, or rename the folder.")
    real = os.path.realpath(root)
    if os.path.commonpath([real, host]) == host:
        raise _Bad(f"The Rookery root {root} is inside the host repo {host}. Use a root outside the repo.")
    if os.path.commonpath([real, host]) == real:
        raise _Bad("The Rookery root must not hold the host repo.")
    return root


def _report_team_errors(args, errors):
    if args.json:
        print(json.dumps({"ok": False, "errors": errors}))
    for e in errors:
        if e.get("index") is None:
            line = e["problem"]
        else:
            line = f"entry {e['index']} ({e.get('name')}): {e['problem']}"
        print(line, file=sys.stderr)
    sys.exit(EXIT_BAD)


def _build_rows(conn, team, host, root, config):
    """One transaction: session, identities, up_agents.
    Return (session, [(agent, 'made'|'reused', changed fields)], notes)."""
    import rookery_identity as ri
    default = config.get("default_binding") or FALLBACK_BINDING
    conn.execute("BEGIN IMMEDIATE")
    try:
        session = ri.get_session(conn, team["session"])
        new_session = session is None
        if new_session:
            session = ri._new_session(conn, team["session"])
        elif session["state"] != "open":
            raise _Bad(f"The session {session['name']!r} has ended. Choose a new session name "
                       "in the team file.")
        notes = []
        if session["state"] == "open":
            held = conn.execute(
                "SELECT value FROM bindings WHERE session_id=? AND owner_type='session' "
                "AND kind=?", (session["session_id"], ROOT_BINDING)).fetchone()
            if held is not None and held[0] != root:
                raise _Bad(f"Session {session['name']} belongs to the project root {held[0]}. "
                           "Choose a new session name in the team file.")
            if held is None:
                conn.execute(
                    "INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, created_at) "
                    "VALUES(?,?,?,?,?,?)", (session["session_id"], "session", session["session_id"],
                                            ROOT_BINDING, root, ri._now_iso()))
                if not new_session:
                    notes.append(f"adopted session {session['name']}")
        rows = []
        for a in team["agents"]:
            ident = ri.get_identity(conn, a["name"], session["session_id"])
            state = "reused"
            if ident is None:
                ident = ri._new_identity(conn, session["session_id"], a["name"])
                state = "made"
            writer = a["role"] == "writer"
            repo = (a.get("repo") or host) if writer else None
            old = conn.execute("SELECT harness, role, repo FROM up_agents WHERE agent_id=?",
                               (ident["agent_id"],)).fetchone()
            changed = []
            if old is not None:
                changed = [k for k, v in (("harness", a["harness"]), ("role", a["role"]),
                                          ("repo", repo)) if old[k] != v]
            conn.execute(
                "INSERT INTO up_agents(agent_id, session_id, harness, role, repo, task_id, "
                "binding_kind, updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET harness=excluded.harness, role=excluded.role, "
                "repo=excluded.repo, task_id=excluded.task_id, binding_kind=excluded.binding_kind, "
                "updated_at=excluded.updated_at",
                (ident["agent_id"], session["session_id"], a["harness"], a["role"],
                 repo, a.get("task_id") if writer else None,
                 a.get("binding") or default, ri._now_iso()))
            rows.append((dict(a, agent_id=ident["agent_id"]), state, changed))
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return session, rows, notes


def _up(args):
    import cli_init
    import rookery_identity
    import rookery_team
    import rookery_views
    import turn_block

    if args.team_file.startswith(("http://", "https://")):
        raise _Bad(URL_MSG)
    try:
        team = rookery_team.load(args.team_file)
    except rookery_team.TeamError as e:
        _report_team_errors(args, e.errors)
    host, project = host_repo(os.getcwd())
    root = _resolve_root(host, project)
    config = rookery_identity.load_config(persist=False)
    launch = ("skipped (--no-launch)" if args.no_launch else "not started: no launch yet")
    try:
        with cli_init.init_lock(root):
            conn = turn_block.open_db()
            try:
                turn_block.ensure_up_tables(conn)
                session, rows, notes = _build_rows(conn, team, host, root, config)
            finally:
                conn.close()
            report = []
            try:
                for a, state, changed in rows:
                    ws = rookery_views.workspace_files(root, session, a, team, host)
                    writer_repo = (a.get("repo") or host) if a["role"] == "writer" else None
                    rookery_views.settings_json(os.path.join(ws, "_config", "settings.json"),
                                                project, repo=writer_repo)
                    report.append({"name": a["name"], "agent_id": a["agent_id"], "role": a["role"],
                                   "workspace": ws, "worktree": None, "launch": launch,
                                   "binding": None, "first_start_dialogs": False,
                                   "changed": changed, "_state": state})
                    if changed:
                        notes.append(f"{a['name']}: changed {', '.join(changed)}")
                md = rookery_views.rookery_md(root)
            except OSError as e:
                raise _Bad(f"Rookery cannot write {e.filename or root}: {e.strerror}. "
                           "Rookery saved the agents. Fix the folder, then run rookery up again.")
            except cli_init.InitError as e:
                raise _Bad(f"{e}. Rookery saved the agents. Fix the folder, then run rookery up again.")
    except cli_init.InitError as e:
        raise _Bad(str(e))
    if args.json:
        agents = [{k: v for k, v in r.items() if k != "_state"} for r in report]
        print(json.dumps({"ok": True, "root": root, "session_id": session["session_id"],
                          "agents": agents, "notes": notes, "errors": []}))
    else:
        for n in notes:
            print(n)
        for r in report:
            print(f"{r['name']}: {r['_state']}")
        print(f"Root: {root}")
        print(f"ROOKERY.md: {md}")


def _fail(args, msg, code):
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "errors": [{"index": None, "name": None, "problem": msg}]}))
    print(msg, file=sys.stderr)
    sys.exit(code)


def cmd_up(args):
    try:
        _up(args)
    except _Bad as e:
        _fail(args, str(e), e.code)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        _fail(args, f"database error: {e}",
              EXIT_BUSY if ("locked" in msg or "busy" in msg) else EXIT_BAD)
    except ValueError as e:
        _fail(args, str(e), EXIT_BAD)


def add_parser(subparsers):
    p = subparsers.add_parser("up", help="Build a team from a team file.")
    p.add_argument("team_file", metavar="TEAM_FILE", help="Path to a JSON team file.")
    p.add_argument("--no-launch", action="store_true", help="Build everything and run no agent.")
    p.add_argument("--json", action="store_true", help="Print one JSON object.")
    p.set_defaults(func=cmd_up)
