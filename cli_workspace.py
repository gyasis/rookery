"""cli_workspace — `rookery up TEAM_FILE`: build a team (specs/003-rookery-up).

Register with: cli_workspace.add_parser(subparsers)
Order of work: research.md D22. This module does steps 1, 2, 3, 5 and 7 (no launch, no worktree yet).
Exit codes: 0 ok, 2 bad input, 4 database busy (as cli_rookery_session).
Never writes ~/.claude.json, ~/.claude/settings.json or any file in the host repo.
"""

import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys

EXIT_BAD, EXIT_BUSY = 2, 4
ROOT_BINDING = "rookery-root"
URL_MSG = "`rookery up` now builds a team. To join a peer, use `rookery join URL`."


class _Bad(Exception):
    def __init__(self, msg, code=EXIT_BAD):
        super().__init__(msg)
        self.code = code


def _git_out(cwd, *args):
    try:
        import rookery_worktree
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10,
                           env=rookery_worktree.git_env())
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


_LAUNCH_KEYS = ("launch", "alive", "harness", "ports", "default_binding")
FIRST_START = "First start: accept the trust and import dialogs in pane {pane}."


def _up_config(config):
    """The user config, with each launch key it lacks taken from the packaged default file."""
    import rookery_identity
    merged = dict(config)
    missing = [k for k in _LAUNCH_KEYS if k not in merged]
    if missing:
        default = json.loads(rookery_identity._read_default_config())
        for k in missing:
            if k in default:
                merged[k] = default[k]
    return merged


def _agent_cwd(a, ws):
    """Working folder of a started agent: a writer's worktree, else the workspace."""
    return a.get("_worktree") or ws


_PLACEHOLDER = re.compile(r"\{(cwd|label|pane|value)\}")


class _LaunchFail(Exception):
    """One agent did not start. The message is the report text after 'not started: '."""


def _fill(token, values):
    """Fill {cwd} {label} {pane} {value} in ONE pass, so a filled value that holds '{pane}' stays text.
    A whole-element {env} or {cmd} is not handled here: _launch_agent expands it in place to many elements."""
    def one(m):
        v = values.get(m.group(1))
        if v is None:
            raise _LaunchFail("{" + m.group(1) + "}")
        return v
    return _PLACEHOLDER.sub(one, token)


def _harness_cmd(a, ws, root, host, hcfg):
    cmd = list(hcfg.get("cmd") or [])
    if a["harness"] == "claude":
        settings = os.path.join(ws, "_config", "settings.json")
        cmd += [hcfg["settings_flag"], settings]
        if a["role"] == "writer":
            cmd += [hcfg["prompt_file_flag"], os.path.join(ws, "_config", "system-prompt.md"),
                    hcfg["add_dir_flag"], ws, hcfg["add_dir_flag"], os.path.join(root, "_directory")]
        else:
            cmd += [hcfg["add_dir_flag"], host]
    elif a["role"] == "writer" and hcfg.get("prompt_flag"):
        with open(os.path.join(ws, "_config", "system-prompt.md"), encoding="utf-8") as fh:
            cmd += [hcfg["prompt_flag"], fh.read()]
    return cmd


def _env_pairs(session, a, root, host):
    pairs = [("ROOKERY_SESSION", session["session_id"]), ("ROOKERY_NODE", a["agent_id"]),
             ("ROOKERY_ROOT", os.path.abspath(root)), ("ROOKERY_PROJECT_ROOT", os.path.abspath(host)),
             ("ROOKERY_PORTS", json.dumps(a.get("_ports") or {}))]
    if os.environ.get("ROOKERY_DB"):
        pairs.append(("ROOKERY_DB", os.path.abspath(os.environ["ROOKERY_DB"])))
    return pairs


def _run(argv, timeout=60):
    """Run argv with no shell. Return (exit code, stdout, text for a report) or raise OSError."""
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, (r.stdout + r.stderr).strip()


def _alive(conn, config, session, a, kind):
    """Return 'running', binding text, or None. Probes only the bindings of the agent's own kind."""
    probe = (config.get("alive") or {}).get(kind)
    if not probe:
        return None
    for b in conn.execute("SELECT kind, value FROM bindings WHERE session_id=? AND owner_type='identity' "
                          "AND owner_id=? AND kind=? ORDER BY created_at, rowid",
                          (session["session_id"], a["agent_id"], kind)).fetchall():
        try:
            code = _run([_fill(t, {"value": b["value"]}) for t in probe], timeout=5)[0]
        except (OSError, subprocess.SubprocessError):
            raise _LaunchFail(f"Rookery cannot tell if pane {b['value']} is alive")
        if code == 0:
            return f"{b['kind']}:{b['value']}"
    return None


def _expand_step(n, kind, step, values, env, cmd):
    if not isinstance(step, dict) or not isinstance(step.get("argv"), list) \
            or not all(isinstance(t, str) for t in step["argv"]):
        raise _LaunchFail(f"invalid launch config for {kind}: step {n} needs an argv list of text")
    argv = []
    for tok in step["argv"]:
        if tok == "{env}":
            if env is None:
                raise _LaunchFail(f"launch config for {kind} has no env_flag")
            argv += env
        elif tok == "{cmd}":
            argv += [shlex.join(cmd)] if step.get("join_cmd") else cmd
        else:
            try:
                argv.append(_fill(tok, values))
            except _LaunchFail as e:
                if str(e) == "{pane}":
                    raise _LaunchFail(f"step {n} uses {{pane}} before a capture")
                raise
    return argv


def _start(conn, config, session, a, ws, root, host, kind):
    """Start one agent. Return (binding text, pane). Raise _LaunchFail with the reason."""
    import rookery_identity
    entry = (config.get("launch") or {}).get(kind)
    if not isinstance(entry, dict) or not entry.get("steps"):
        raise _LaunchFail(f"no launch command for {kind}")
    steps = entry["steps"]
    if not isinstance(steps, list):
        raise _LaunchFail(f"invalid launch config for {kind}: steps must be a list")
    hcfg = (config.get("harness") or {}).get(a["harness"])
    if not hcfg:
        raise _LaunchFail(f"no harness command for {a['harness']}")
    try:
        cmd = _harness_cmd(a, ws, root, host, hcfg)
    except (OSError, KeyError) as e:
        raise _LaunchFail(f"cannot build the {a['harness']} command: {e}")
    flag = entry.get("env_flag")
    env = None
    if flag:
        env = []
        for k, v in _env_pairs(session, a, root, host):
            env += [flag, f"{k}={v}"]
    values = {"cwd": os.path.abspath(_agent_cwd(a, ws)), "label": f"{session['name']}/{a['name']}"}
    pane = None
    for n, step in enumerate(steps, 1):
        argv = _expand_step(n, kind, step, dict(values, **({"pane": pane} if pane else {})), env, cmd)
        pattern = step.get("capture")
        if pattern is not None:
            try:
                rx = re.compile(pattern, re.M)
                if rx.groups < 1:
                    raise re.error("the pattern has no group")
            except (re.error, TypeError) as e:
                raise _LaunchFail(f"invalid launch config for {kind}: step {n} capture: {e}")
        try:
            code, out, text = _run(argv)
        except (OSError, subprocess.SubprocessError) as e:
            raise _LaunchFail(f"step {n} could not run: {e}")
        if code != 0:
            if pane:
                raise _LaunchFail(f"step {n} exited {code}. Pane {pane} is open. "
                                  "Close it, then run rookery up again.")
            raise _LaunchFail(f"step {n} exited {code}: {text}")
        if pattern is not None:
            m = rx.search(out.strip())
            if not m:
                raise _LaunchFail(f"no pane ID in step {n} output: {text}")
            pane = m.group(1)
    if pane is None:
        raise _LaunchFail("no step captured a pane ID")
    # A pane ID that another identity of this session holds moves to this agent (finding 8, by design).
    rookery_identity.bind(conn, session["session_id"], a["agent_id"], kind, pane, root)
    return f"{kind}:{pane}", pane


def _launch_agent(conn, config, session, a, ws, root, host):
    """Return (launch text, binding text or None, pane or None). Never raises for one agent's failure."""
    try:
        kind = a.get("binding") or config.get("default_binding")
        if not kind:
            raise _LaunchFail("no binding kind is set")
        live = _alive(conn, config, session, a, kind)
        if live:
            return "running", live, None
        binding, pane = _start(conn, config, session, a, ws, root, host, kind)
        return "started", binding, pane
    except _LaunchFail as e:
        return f"not started: {e}", None, None
    except Exception as e:     # one agent must never stop the others (FR-018)
        return f"not started: {type(e).__name__}: {e}", None, None


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


def _store_claims(conn, session_id, agent_id, patterns):
    """Team file claims -> claims rows: new patterns active, kept ones active, dropped ones inactive.
    A row is never deleted."""
    import rookery_identity as ri
    now = ri._now_iso()
    for p in dict.fromkeys(patterns):
        conn.execute("INSERT INTO claims(session_id, agent_id, pattern, active, updated_at) VALUES(?,?,?,1,?) "
                     "ON CONFLICT(session_id, agent_id, pattern) DO UPDATE SET active=1, updated_at=excluded.updated_at "
                     "WHERE active=0", (session_id, agent_id, p, now))
    for (p, active) in conn.execute("SELECT pattern, active FROM claims WHERE session_id=? AND agent_id=?",
                                    (session_id, agent_id)).fetchall():
        if active and p not in patterns:
            conn.execute("UPDATE claims SET active=0, updated_at=? WHERE session_id=? AND agent_id=? AND pattern=?",
                         (now, session_id, agent_id, p))


def _build_rows(conn, team, host, root, config):
    """One transaction: session, identities, up_agents.
    Return (session, [(agent, 'made'|'reused', changed fields)], notes)."""
    import rookery_identity as ri
    default = config.get("default_binding") or ""
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
            _store_claims(conn, session["session_id"], ident["agent_id"], a.get("claims") or [])
            row = dict(a, agent_id=ident["agent_id"])
            if writer:
                import rookery_worktree
                row["_wt"] = rookery_worktree.register(
                    conn, root, session["name"], session["session_id"], a["name"], ident["agent_id"],
                    repo, a["task_id"], ri._now_iso())
            rows.append((row, state, changed))
        names = [a["name"] for a in team["agents"]]
        for (aid,) in conn.execute(
                "SELECT u.agent_id FROM up_agents u JOIN identities i ON i.agent_id=u.agent_id "
                "WHERE u.session_id=? AND i.name NOT IN (%s)" % ",".join("?" * len(names)),
                (session["session_id"], *names)).fetchall():
            conn.execute("UPDATE claims SET active=0, updated_at=? WHERE session_id=? AND agent_id=? AND active=1",
                         (ri._now_iso(), session["session_id"], aid))
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
    import rookery_worktree
    import turn_block

    if args.team_file.startswith(("http://", "https://")):
        raise _Bad(URL_MSG)
    try:
        team = rookery_team.load(args.team_file, _up_config(rookery_identity.load_config(persist=False)))
    except rookery_team.TeamError as e:
        _report_team_errors(args, e.errors)
    host, project = host_repo(os.getcwd())
    root = _resolve_root(host, project)
    config = rookery_identity.load_config(persist=False)
    launch = "skipped (--no-launch)"
    try:
        with cli_init.init_lock(root):
            conn = turn_block.open_db()
            try:
                turn_block.ensure_up_tables(conn)
                try:
                    rookery_worktree.check_session_free(conn, root, team["session"])
                except rookery_worktree.Refusal as e:
                    raise _Bad(str(e))
                try:
                    for a in team["agents"]:
                        if a["role"] == "writer":
                            rookery_worktree.check_agent_free(conn, root, team["session"], a["name"],
                                                              a.get("repo") or host)
                except rookery_worktree.Refusal as e:
                    raise _Bad(str(e))
                session, rows, notes = _build_rows(conn, team, host, root, _up_config(config))
            finally:
                conn.close()
            report = []
            try:
                conn = turn_block.open_db()
                try:
                    for a, _s, _c in rows:
                        if a.get("_wt"):
                            a["_worktree"], a["_wt_err"], note = rookery_worktree.add(conn, a["_wt"])
                            notes.extend(a["_wt"]["notes"])
                            if note:
                                notes.append(note)
                    import rookery_guard
                    ucfg = _up_config(config)
                    for a, _s, _c in rows:
                        try:
                            a["_ports"] = rookery_guard.assign_ports(conn, a["agent_id"], ucfg)
                        except rookery_guard.PortError as e:
                            a["_port_err"] = str(e)
                finally:
                    conn.close()
                for a, state, changed in rows:
                    ws = rookery_views.workspace_files(root, session, a, team, host, notes=notes)
                    writer_repo = (a.get("repo") or host) if a["role"] == "writer" else None
                    rookery_views.settings_json(os.path.join(ws, "_config", "settings.json"),
                                                project, repo=writer_repo)
                    report.append({"name": a["name"], "agent_id": a["agent_id"], "role": a["role"],
                                   "workspace": ws, "worktree": a.get("_worktree"), "launch": launch,
                                   "binding": None, "first_start_dialogs": False,
                                   "changed": changed, "_state": state})
                    if a.get("_wt_err"):
                        report[-1]["launch"] = f"not started: {a['_wt_err']}"
                    elif a.get("_port_err"):
                        report[-1]["launch"] = f"not started: {a['_port_err']}"
                    if changed:
                        notes.append(f"{a['name']}: changed {', '.join(changed)}")
                md = rookery_views.rookery_md(root)
                conn = turn_block.open_db()
                try:
                    rookery_views.teams(conn, root, session, team)
                    rookery_views.directory(conn, root, "up")
                    rookery_views.threads(conn, root, "up")
                    rookery_views.mail_mirror(conn, root, "up")
                finally:
                    conn.close()
                if not args.no_launch:
                    ucfg = _up_config(config)
                    conn = turn_block.open_db()
                    try:
                        for r, (a, _s, _c) in zip(report, rows):
                            if a.get("_wt_err") or a.get("_port_err"):
                                continue
                            r["launch"], r["binding"], pane = _launch_agent(
                                conn, ucfg, session, a, r["workspace"], root, host)
                            if r["launch"] == "started" and a["harness"] == "claude":
                                r["first_start_dialogs"] = True
                                r["_pane"] = pane
                    finally:
                        conn.close()
            except OSError as e:
                raise _Bad(f"Rookery cannot write {e.filename or root}: {e.strerror}. "
                           "Rookery saved the agents. Fix the folder, then run rookery up again.")
            except cli_init.InitError as e:
                raise _Bad(f"{e}. Rookery saved the agents. Fix the folder, then run rookery up again.")
    except cli_init.InitError as e:
        raise _Bad(str(e))
    if args.json:
        agents = [{k: v for k, v in r.items() if k not in ("_state", "_pane")} for r in report]
        ok = all(r["launch"] in ("started", "running") for r in report) or (
            args.no_launch and not any(r["launch"].startswith("not started") for r in report))
        print(json.dumps({"ok": ok, "root": root, "session_id": session["session_id"],
                          "agents": agents, "notes": notes, "errors": []}))
    else:
        for n in notes:
            print(n)
        for r in report:
            show = not args.no_launch or r["launch"].startswith("not started")
            print(f"{r['name']}: {r['_state']}" + (f"; {r['launch']}" if show else ""))
            if r.get("_pane"):
                print(FIRST_START.format(pane=r["_pane"]))
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


def _with_db(args, fn):
    import turn_block
    conn = turn_block.open_db()
    try:
        turn_block.ensure_up_tables(conn)
        return fn(conn)
    finally:
        conn.close()


def _root_or_none():
    """The Rookery root for the cwd (as `rookery up` finds it), else ROOKERY_ROOT, else None (no filter)."""
    try:
        host, project = host_repo(os.getcwd())
        return _resolve_root(host, project)
    except _Bad:
        env_root = os.environ.get("ROOKERY_ROOT")
        return os.path.abspath(env_root) if env_root else None


def cmd_finish(args):
    import rookery_worktree
    session = args.session or os.environ.get("ROOKERY_SESSION")
    try:
        kw = {}
        if os.environ.get("ROOKERY_FINISH_TIMEOUT"):
            kw["timeout"] = float(os.environ["ROOKERY_FINISH_TIMEOUT"])
        res = _with_db(args, lambda conn: rookery_worktree.finish(
            conn, _root_or_none(), session, args.name, args.message,
            out=sys.stderr if args.json else sys.stdout, **kw))
    except (rookery_worktree.Refusal, ValueError) as e:
        _fail(args, str(e), EXIT_BAD)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        _fail(args, f"database error: {e}", EXIT_BUSY if ("locked" in msg or "busy" in msg) else EXIT_BAD)
    if args.json:
        print(json.dumps(dict(res, ok=True)))


def cmd_gc(args):
    import rookery_worktree
    try:
        found = _with_db(args, lambda conn: rookery_worktree.gc(conn, _root_or_none()))
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        _fail(args, f"database error: {e}", EXIT_BUSY if ("locked" in msg or "busy" in msg) else EXIT_BAD)
    if args.json:
        print(json.dumps(found))
        return
    if not found:
        print("No stale worktree rows.")
    for f in found:
        print(f"{f['path']}\t{f['session']}\t{f['agent']}\t{f['branch']}\t{f['reason']}")


def cmd_regen(args):
    import rookery_views
    root = _root_or_none()
    if root is None:
        _fail(args, "Set ROOKERY_ROOT or run from the host repo.", EXIT_BAD)

    def go(conn):
        paths = [rookery_views.directory(conn, root), rookery_views.threads(conn, root)]
        paths += rookery_views.mail_mirror(conn, root)
        return paths
    try:
        paths = _with_db(args, go)
    except OSError as e:
        _fail(args, f"Rookery cannot write {e.filename or root}: {e.strerror}.", EXIT_BAD)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        _fail(args, f"database error: {e}", EXIT_BUSY if ("locked" in msg or "busy" in msg) else EXIT_BAD)
    if args.json:
        print(json.dumps({"ok": True, "root": root, "files": paths}))
    else:
        print(f"Wrote {len(paths)} files under {root}.")


def add_parser(subparsers):
    p = subparsers.add_parser("up", help="Build a team from a team file.")
    p.add_argument("team_file", metavar="TEAM_FILE", help="Path to a JSON team file.")
    p.add_argument("--no-launch", action="store_true", help="Build everything and run no agent.")
    p.add_argument("--json", action="store_true", help="Print one JSON object.")
    p.set_defaults(func=cmd_up)
    f = subparsers.add_parser("finish", help="Commit, push and remove a writer's worktree.")
    f.add_argument("name", metavar="NAME", help="The writer's name.")
    f.add_argument("--session", help="Session name or ID (default: ROOKERY_SESSION).")
    f.add_argument("--message", required=True, help="Commit message.")
    f.add_argument("--json", action="store_true", help="Print one JSON object.")
    f.set_defaults(func=cmd_finish)
    g = subparsers.add_parser("gc", help="List stale worktree rows. Deletes nothing.")
    g.add_argument("--json", action="store_true", help="Print a JSON list.")
    g.set_defaults(func=cmd_gc)
    r = subparsers.add_parser("regen", help="Write the generated views again from the database.")
    r.add_argument("--json", action="store_true", help="Print one JSON object.")
    r.set_defaults(func=cmd_regen)
