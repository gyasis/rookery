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
import unicodedata


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
            return                          # no Rookery environment: write nothing (SC-010)
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
                return                      # resolve logged at most one line
            session, identity, _bindings = resolved
            text = hook_input.get("last_assistant_message")
            if session["state"] != "open":
                _keep_ended(turn_block, root, session, identity, text)
                return                      # sends nothing, no idle write, no drain
            if not isinstance(text, str):
                turn_block.log_event(
                    root, adapter="stop", session=session["session_id"],
                    outcome="no-message-field",
                    reason="Stop JSON has no last_assistant_message")
                _write_idle(turn_block, root, session, identity)
                return
            if identity is None:
                _keep_unresolved(turn_block, root, session, text)
                return
            ctx = {"root": root, "session": session, "identity": identity,
                   "adapter": "stop"}
            turn_block.dispatch_text(conn, text, ctx)
            _write_idle(turn_block, root, session, identity)
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


def _keep_ended(turn_block, root, session, identity, text):
    """Stop in an ended session: send nothing. Keep a valid block under outbox/failed
    (as the outbox scan does for `session-ended`) and log `session-ended` once."""
    if identity is None:
        _keep_unresolved(turn_block, root, session, text if isinstance(text, str) else "",
                         outcome="session-ended", reason="the session has ended")
        return
    kept = None
    if isinstance(text, str):
        raw = turn_block.find_last_block(text)
        if raw is not None:
            block, _note = turn_block.validate(raw, root)
            ctx = {"root": root, "session": session, "identity": identity}
            kept = turn_block._put_back(ctx, block, raw, failed=True)
    turn_block.log_event(root, adapter="stop", session=session["session_id"],
                         agent_id=identity["agent_id"], outcome="session-ended",
                         reason="the session has ended", kept=kept is not None)


def _write_idle(turn_block, root, session, identity):
    """FR-017, D8: the turn ended, so the identity is idle and the postmaster may push.
    Never prints and never raises: a failed write is logged (no outcome field, so the
    call still has exactly one outcome line)."""
    if identity is None:
        return
    try:
        import rookery
        ws = turn_block.workspace(root, session["name"], identity["name"])
        turn_block.write_json_atomic(os.path.join(ws, "state", "turn.json"),
                                     {"state": "idle", "since": rookery.now()})
    except Exception as exc:
        _safe_log(root, event="idle-write-failed", session=session["session_id"],
                  agent_id=identity["agent_id"], reason=f"{type(exc).__name__}: {exc}")


def _safe_log(root, **fields):
    if root is None:
        return
    try:
        import turn_block
        fields.setdefault("adapter", "stop")
        turn_block.log_event(root, **fields)
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


_HANDOFF_CAP = 16 * 1024    # bytes of HANDOFF.md printed into context


def _newest_handoff(ws):
    """Text of the newest runs/*/HANDOFF.md (by mtime) inside the workspace, or None.
    Symlinks (file or run folder) and paths that resolve outside the workspace are skipped."""
    runs = os.path.join(ws, "runs")
    real_ws = os.path.realpath(ws)
    best = None
    try:
        names = os.listdir(runs)
    except OSError:
        return None
    for n in names:
        d = os.path.join(runs, n)
        p = os.path.join(d, "HANDOFF.md")
        try:
            if os.path.islink(d) or os.path.islink(p) or not os.path.isfile(p):
                continue
            if os.path.commonpath([real_ws, os.path.realpath(p)]) != real_ws:
                continue
            m = os.path.getmtime(p)
        except (OSError, ValueError):
            continue
        if best is None or (m, p) > best:
            best = (m, p)
    if best is None:
        return None
    with open(best[1], "rb") as fh:
        raw = fh.read(_HANDOFF_CAP + 1)
    text = raw[:_HANDOFF_CAP].decode("utf-8", errors="ignore")   # cut mid-char: drop, no U+FFFD
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    import turn_block
    text = "".join(ch for ch in text if ch in "\n\t"
                   or unicodedata.category(ch) not in turn_block._DROP_CATEGORIES)
    text = text.rstrip("\n")
    if len(raw) > _HANDOFF_CAP:
        text += f"\n[rookery] HANDOFF.md truncated at {_HANDOFF_CAP} bytes."
    return text or None


def _env_value(v):
    return "".join(str(v).split())      # IDs are safe; never let a newline through


_ENV_FILE_CAP = 1024 * 1024


def _write_env_file(path, session_id, agent_id):
    """Append the two exports unless they are already the last ROOKERY_* exports in the file.
    A missing trailing newline is repaired first so the previous line is never corrupted."""
    pair = [f"export ROOKERY_SESSION={_env_value(session_id)}",
            f"export ROOKERY_NODE={_env_value(agent_id)}"]
    existing = ""
    try:
        with open(path, "rb") as fh:
            existing = fh.read(_ENV_FILE_CAP).decode("utf-8", errors="replace")
    except FileNotFoundError:
        pass
    last = [ln for ln in existing.splitlines() if ln.startswith("export ROOKERY_")]
    if last[-2:] == pair:
        return
    prefix = "\n" if existing and not existing.endswith("\n") else ""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(prefix + "\n".join(pair) + "\n")


def _resolve_buffered(turn_block, rookery_identity, conn, hook_input, root, create=True):
    """resolve(create=...), holding its log events back so a failing call logs ONE line.
    Returns (events, resolved)."""
    events = []
    orig = turn_block.log_event
    turn_block.log_event = lambda _root, stream="turn-block", **f: events.append((stream, f))
    try:
        resolved = rookery_identity.resolve(
            conn, os.environ, hook_input, root, create=create)
    finally:
        turn_block.log_event = orig
    return events, resolved


def _flush_events(turn_block, root, events):
    for stream, fields in events:
        turn_block.log_event(root, stream=stream, **fields)


def session_start(args):
    """SessionStart hook (FR-015, D9, D16, D20). Creates identity/bindings, prints context."""
    root = None
    session_id = None
    events = []
    ids = {}
    turn_block = None
    try:
        import turn_block
        import rookery_identity
        import rookery

        if _no_session_possible(turn_block, rookery_identity):
            return                          # no Rookery environment: write nothing (SC-010)
        root = turn_block.project_root(args.project)
        hook_input, bad = _read_input()
        if bad:
            hook_input = {}                 # identity may still come from the environment
        conn = turn_block.open_db()
        try:
            ended = rookery_identity.find_session(
                conn, os.environ, rookery_identity.load_config(persist=False))
            if ended is not None and ended["state"] != "open":
                turn_block.log_event(root, adapter="session-start",
                                     session=ended["session_id"], outcome="session-ended")
                return                      # the session is over: create and print nothing
            try:
                events, resolved = _resolve_buffered(
                    turn_block, rookery_identity, conn, hook_input, root)
            except rookery_identity.SessionEnded:       # ended between the check and the lock
                turn_block.log_event(root, adapter="session-start",
                                     session=ended["session_id"] if ended else None,
                                     outcome="session-ended")
                return
            if resolved is None:
                _flush_events(turn_block, root, events)
                return
            session, identity, _bindings = resolved
            if identity is None:
                return
            session_id = session["session_id"]
            agent_id, name = identity["agent_id"], identity["name"]
            ids = {"agent_id": agent_id, "name": name}
            rookery.register_node(conn, agent_id, kind="pane", lifecycle="persistent")
            lines = [f"[rookery] You are {name} ({agent_id}) in Rookery session "
                     f"{session['name']}."]
            ws = turn_block.workspace(root, session["name"], name)
            env_file = os.environ.get("CLAUDE_ENV_FILE")
            if env_file:
                _write_env_file(env_file, session_id, agent_id)
            # A fresh session cannot be mid-turn, but compact and resume can fire inside one:
            # reset only on startup / clear (or an unknown source), and keep a live busy otherwise.
            tpath = os.path.join(ws, "state", "turn.json")
            prev = None
            try:
                with open(tpath) as fh:
                    prev = json.load(fh).get("state")
            except (OSError, ValueError, AttributeError):
                pass
            src = hook_input.get("source")
            reset_state = None
            if src in (None, "startup", "clear") or prev is None:
                turn_block.write_json_atomic(tpath, {"state": "idle", "since": rookery.now()})
                reset_state = prev == "busy"
            handoff = _newest_handoff(ws)
            if handoff:
                lines.append(handoff)
            unread = turn_block.format_unread_line(turn_block.unread_summary(conn, agent_id))
            if unread:
                lines.append(unread)
            failed = turn_block.new_failed_blocks(root, session["name"], name)
            lines.extend(turn_block.format_failed_line(it) for it in failed)
        finally:
            conn.close()
        sys.stdout.write("\n".join(lines) + "\n")     # one write: nothing partial on failure
        sys.stdout.flush()
        if failed:
            turn_block.mark_failed_seen(root, session["name"], name,
                                        [it["file"] for it in failed])
        _flush_events(turn_block, root, events)
        if reset_state:
            turn_block.log_event(root, adapter="session-start", session=session_id,
                                 agent_id=agent_id, name=name, outcome="state-reset",
                                 reason="session-start")
        turn_block.log_event(root, adapter="session-start", session=session_id,
                             agent_id=agent_id, outcome="session-start",
                             source=hook_input.get("source"), failed_shown=len(failed))
    except Exception as exc:                # D16: log and exit 0, no stdout
        # keep the audit trail of identities already committed, then ONE hook-error line
        try:
            _flush_events(turn_block, root, events)
        except Exception:
            pass
        _safe_log(root, adapter="session-start", session=session_id, **ids,
                  outcome="hook-error", reason=f"{type(exc).__name__}: {exc}")


PUSH_PROMPT_PREFIX = "[rookery] new mail for"   # the postmaster push text (postmaster._notice)


_PASTE_START, _PASTE_END = "\x1b[200~", "\x1b[201~"


def _is_push_prompt(text) -> bool:
    """True when text is not a human prompt: a postmaster push, or no prompt text at all (nothing
    resets then). Leading white space and one bracketed-paste wrapper are ignored."""
    if not isinstance(text, str):
        return True
    text = text.lstrip()
    if text.startswith(_PASTE_START):
        text = text[len(_PASTE_START):].lstrip()
    return text.startswith(PUSH_PROMPT_PREFIX)


def prompt(args):
    """UserPromptSubmit hook (FR-016, FR-037, D8, D16, D23). Runs on every prompt: read-only
    resolve (never creates an identity), writes turn.json = busy, prints unread/failed lines.
    A prompt that is not a postmaster push also resets the loop counters of this identity (FR-039)."""
    root = None
    session_id = None
    events = []
    ids = {}
    turn_block = None
    try:
        import turn_block
        import rookery_identity
        import rookery

        if _no_session_possible(turn_block, rookery_identity):
            return                          # no Rookery environment: write nothing (SC-010)
        root = turn_block.project_root(args.project)
        hook_input, bad = _read_input()
        if bad:
            hook_input = {}                 # identity may still come from the environment
        conn = turn_block.open_db()
        try:
            ended = rookery_identity.find_session(
                conn, os.environ, rookery_identity.load_config(persist=False))
            if ended is not None and ended["state"] != "open":
                turn_block.log_event(root, adapter="prompt",
                                     session=ended["session_id"], outcome="session-ended")
                return                      # the session is over: write and print nothing
            events, resolved = _resolve_buffered(
                turn_block, rookery_identity, conn, hook_input, root, create=False)
            if resolved is None:
                _flush_events(turn_block, root, events)
                return
            session, identity, _bindings = resolved
            if identity is None:
                _flush_events(turn_block, root, events)
                turn_block.log_event(root, adapter="prompt", session=session["session_id"],
                                     outcome="no-identity")
                return
            session_id = session["session_id"]
            agent_id, name = identity["agent_id"], identity["name"]
            ids = {"agent_id": agent_id, "name": name}
            ws = turn_block.workspace(root, session["name"], name)
            busy_doc = {"state": "busy", "since": rookery.now()}
            tr = hook_input.get("transcript_path")      # liveness for the postmaster (FR-040)
            if isinstance(tr, str) and tr:
                busy_doc["transcript"] = tr
            turn_block.write_json_atomic(os.path.join(ws, "state", "turn.json"), busy_doc)
            reset = 0
            if not _is_push_prompt(hook_input.get("prompt")):
                try:
                    reset = turn_block.reset_loop_counts(conn, agent_id)   # a human prompt (FR-039)
                except Exception as exc:     # never lose the notice over the counter
                    _safe_log(root, adapter="prompt", session=session_id, **ids,
                              outcome="loop-reset-error", reason=f"{type(exc).__name__}: {exc}")
            lines = []
            unread = turn_block.format_unread_line(turn_block.unread_summary(conn, agent_id))
            if unread:
                lines.append(unread)
            failed = turn_block.new_failed_blocks(root, session["name"], name)
            lines.extend(turn_block.format_failed_line(it) for it in failed)
        finally:
            conn.close()
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")   # one write; 0 bytes when nothing to say
            sys.stdout.flush()
        if failed:
            turn_block.mark_failed_seen(root, session["name"], name,
                                        [it["file"] for it in failed])
        _flush_events(turn_block, root, events)
        if reset:
            turn_block.log_event(root, adapter="prompt", session=session_id,
                                 agent_id=agent_id, outcome="loop-reset", counters=reset)
        turn_block.log_event(root, adapter="prompt", session=session_id,
                             agent_id=agent_id, outcome="prompt",
                             unread_shown=bool(unread), failed_shown=len(failed))
    except Exception as exc:                # D16: log and exit 0, no stdout
        try:
            _flush_events(turn_block, root, events)
        except Exception:
            pass
        _safe_log(root, adapter="prompt", session=session_id, **ids,
                  outcome="hook-error", reason=f"{type(exc).__name__}: {exc}")


_TURN_MAX_BYTES = 1024 * 1024   # stdin cap for `rookery turn`
_NO_SESSION_MSG = ("No current Rookery session. Set ROOKERY_SESSION or start in a pane "
                   "with a session binding.")


def _turn_exit(args, code, outcome, reason, msg_id=None, human=None):
    """Print the result and exit. --json: one object on stdout. Errors (code != 0)
    also put one line on stderr. Without --json: one human line."""
    if code != 0:
        print(human or reason, file=sys.stderr)
    if getattr(args, "json", False):
        print(json.dumps({"outcome": outcome, "msg_id": msg_id, "reason": reason}))
    elif code == 0:
        print(human or (f"{outcome}: {reason}" if reason else outcome))
    sys.exit(code)


def _turn_keep_busy(root, text):
    """D16: resolve hit a lock. A read does not wait in WAL mode, so resolve read-only;
    with an identity, put the valid block back in outbox/. True when it was kept."""
    try:
        import turn_block
        import rookery_identity
        raw = turn_block.find_last_block(text)
        if raw is None:
            return False
        block, _note = turn_block.validate(raw, root)
        conn = turn_block.open_db()
        try:
            resolved = rookery_identity.resolve(conn, os.environ, {}, root, create=False)
        finally:
            conn.close()
        if resolved is None or resolved[1] is None:
            return False
        ctx = {"root": root, "session": resolved[0], "identity": resolved[1], "adapter": "turn"}
        turn_block._put_back(ctx, block, raw, failed=False)
        turn_block.log_event(root, adapter="turn", session=resolved[0]["session_id"],
                             agent_id=resolved[1]["agent_id"], outcome="db-busy",
                             reason="resolve hit a lock; block kept in outbox")
        return True
    except Exception:
        return False


def turn(args):
    """`rookery turn --project NAME [--json]` (FR-020, D3, D16, D20). stdin = the raw
    text of one headless turn. Same dispatch_text() as the Stop hook, adapter `turn`;
    writes no idle state. Exit: 0 done, 2 bad input, 3 no/ended session, 4 db-busy."""
    try:
        import turn_block
        import rookery_identity

        raw = sys.stdin.buffer.read(_TURN_MAX_BYTES + 1)
        if len(raw) > _TURN_MAX_BYTES:
            _turn_exit(args, 2, "bad-input", "input too large",
                       human=f"Input too large. The limit is {_TURN_MAX_BYTES} bytes. "
                             "Send only the final message of the turn.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            _turn_exit(args, 2, "bad-input", "input is not valid UTF-8",
                       human="Input is not valid UTF-8. Rookery sent nothing.")
        if not text.strip():
            _turn_exit(args, 2, "bad-input", "empty stdin",
                       human="Empty input. Pipe the text of the turn into rookery turn.")
        if _no_session_possible(turn_block, rookery_identity):
            _turn_exit(args, 3, "no-session", "no session", human=_NO_SESSION_MSG)
        root = turn_block.project_root(args.project)

        def on_busy():
            kept = _turn_keep_busy(root, text)
            return ("The database is busy. "
                    + ("The block is back in your outbox. " if kept else "")
                    + "Run rookery turn again with the same text.")

        res = _dispatch_cli(args, root, "turn",
                            lambda conn, ctx: turn_block.dispatch_text(conn, text, ctx), on_busy)
        outcome = res.get("outcome")
        if outcome == "db-busy":
            _turn_exit(args, 4, outcome, res.get("reason"), res.get("msg_id"),
                       human="The database is busy. The block is back in your outbox. "
                             "Run rookery turn again with the same text.")
        if outcome == "loop-held":    # exit 0 like quarantined and send-failed; a note on stderr
            print("loop-held: too many mails with no human prompt. Rookery held this mail. "
                  "Do not send it again until a human gives a prompt.", file=sys.stderr)
        _turn_exit(args, 0, outcome, res.get("reason"), res.get("msg_id"))
    except SystemExit:
        raise
    except Exception as exc:
        _turn_exit(args, 2, "error", f"{type(exc).__name__}: {exc}",
                   human=f"rookery turn failed: {type(exc).__name__}: {exc}")


def _dispatch_cli(args, root, adapter, run, on_busy, create=True):
    """Resolve the current session and identity, then return run(conn, ctx). Shared by
    `rookery turn` and `rookery send`. create=False never makes a new identity.
    Exits 3 on no/ended session, 2 on no identity, 4 when the database is busy
    (on_busy() gives the message)."""
    import turn_block
    import rookery_identity
    try:
        conn = turn_block.open_db()
        try:
            ended = rookery_identity.find_session(
                conn, os.environ, rookery_identity.load_config(persist=False))
            if ended is not None and ended["state"] != "open":
                turn_block.log_event(root, adapter=adapter, session=ended["session_id"],
                                     outcome="session-ended")
                _turn_exit(args, 3, "no-session", "session-ended",
                           human=f"The Rookery session \"{ended['name']}\" has ended. "
                                 "Start a new one with: rookery session new")
            try:
                resolved = rookery_identity.resolve(
                    conn, os.environ, {}, root, create=create)
            except rookery_identity.SessionEnded as exc:   # ended between check and lock
                turn_block.log_event(root, adapter=adapter, outcome="session-ended")
                _turn_exit(args, 3, "no-session", "session-ended", human=str(exc))
            if resolved is None:
                _turn_exit(args, 3, "no-session", "no session", human=_NO_SESSION_MSG)
            session, identity, _bindings = resolved
            if identity is None:
                _turn_exit(args, 2, "bad-input", "no identity",
                           human="No agent identity found. Set ROOKERY_NODE to an "
                                 "agent name in the session.")
            ctx = {"root": root, "session": session, "identity": identity,
                   "adapter": adapter}
            return run(conn, ctx)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "locked" in msg or "busy" in msg:
            _turn_exit(args, 4, "db-busy", str(exc), human=on_busy())
        raise


def send(args):
    """`rookery send TO SAY [--task T] [--kind K] ...` builds one block and sends it
    through the same dispatch_block() gate as the Stop hook and `rookery turn`, adapter
    `send`. The sender is always the resolved identity; there is no --as or --from.
    Exit: 0 sent, 1 not sent (quarantined, held, ...), 2 bad input, 3 no/ended session,
    4 db-busy."""
    try:
        import turn_block
        import rookery_identity

        block = {"to": args.to, "task": args.task, "kind": args.kind, "say": args.say,
                 "status": args.status}
        if args.thread:
            block["thread"] = args.thread
        if args.read:
            block["read"] = args.read
        raw = json.dumps(block, ensure_ascii=False)
        root = turn_block.project_root(args.project or os.path.basename(os.getcwd()))
        _block, note = turn_block.validate(raw, root)
        if note:    # the sender is right here: refuse, store nothing, no bounce
            _turn_exit(args, 2, "bad-input", note, human=note)
        if _no_session_possible(turn_block, rookery_identity):
            _turn_exit(args, 3, "no-session", "no session", human=_NO_SESSION_MSG)
        res = _dispatch_cli(
            args, root, "send",
            lambda conn, ctx: turn_block.dispatch_block(conn, raw, ctx),
            lambda: "The database is busy. Rookery sent nothing. Run rookery send again.",
            create=False)    # sending never registers a new sender (rk did)
        outcome, reason, msg_id = res.get("outcome"), res.get("reason"), res.get("msg_id")
        if outcome == "sent":
            _turn_exit(args, 0, outcome, None, msg_id, human=f"sent #{msg_id} to {args.to}")
        if outcome == "db-busy":
            _turn_exit(args, 4, outcome, reason, msg_id,
                       human="The database is busy. Rookery sent nothing. Run rookery send again.")
        human = {
            "quarantined": f"Not sent. Rookery quarantined the mail ({reason}). "
                           "The bounce in rookery inbox tells you what to fix.",
            "loop-held": "Not sent. Too many mails with no human prompt. Rookery held this "
                         "mail. Do not send it again until a human gives a prompt.",
        }.get(outcome, f"Not sent: {outcome}" + (f": {reason}" if reason else ""))
        _turn_exit(args, 1, outcome, reason, msg_id, human=human)
    except SystemExit:
        raise
    except Exception as exc:
        _turn_exit(args, 2, "error", f"{type(exc).__name__}: {exc}",
                   human=f"rookery send failed: {type(exc).__name__}: {exc}")


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

    tp = sub.add_parser(
        "turn", help="Send the block in a headless turn's text (stdin). Exit 3 no session, 4 db busy.")
    tp.add_argument("--project", required=True, help="Rookery project name")
    tp.add_argument("--json", action="store_true", help="Print one JSON object.")
    tp.set_defaults(func=turn)

    sp = sub.add_parser(
        "send", help="Send mail to an agent in my session. Exit 1 not sent, 3 no session.")
    sp.add_argument("to", metavar="TO", help="Agent name in my session.")
    sp.add_argument("say", metavar="SAY", help="The message (the block's \"say\").")
    sp.add_argument("--task", default="message", help="Task name (default: message).")
    sp.add_argument("--kind", default="ask", help="ask, answer, decline, handoff, status "
                    "or recruit (default: ask).")
    sp.add_argument("--status", default="done", help="done or in-progress (default: done).")
    sp.add_argument("--thread", help="Thread name.")
    sp.add_argument("--read", action="append", metavar="PATH",
                    help="A file the recipient reads. Repeatable.")
    sp.add_argument("--project", help="Rookery project name (default: the current folder).")
    sp.add_argument("--json", action="store_true", help="Print one JSON object.")
    sp.set_defaults(func=send)
