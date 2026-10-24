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
import stat
import sys
import tempfile
import threading
import time
import unicodedata

import rookery

SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")

_LOG_FILES = {
    "turn-block": os.path.join("logs", "turn-block.jsonl"),
    "postmaster": os.path.join("postmaster", "postmaster.jsonl"),
    "gate": os.path.join("logs", "gate.jsonl"),
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


_LOGGER_LOCK = threading.Lock()


def _logger_for(path: str) -> logging.Logger:
    with _LOGGER_LOCK:      # the mailroom and the shared MCP server are threaded
        return _logger_for_locked(path)


def _logger_for_locked(path: str) -> logging.Logger:
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


def gate_root() -> str:
    """ROOKERY_ROOT, else ~/.rookery. The mailroom and the shared MCP server have no project name (D15)."""
    return os.environ.get("ROOKERY_ROOT") or os.path.expanduser("~/.rookery")


def log_event(root, stream: str = "turn-block", **fields) -> None:
    """Append one JSON line (ts + fields) to the stream's rotated log (D15).

    Fields by convention: session, agent_id, name, task, adapter, outcome,
    reason, msg_id. Logging never raises. The `gate` stream takes root=None
    (see gate_root) and prints one line to stderr when a write fails.
    """
    try:
        if root is None:
            root = gate_root()
        rel = _LOG_FILES[stream]
        rec = {"ts": rookery.now()}
        rec.update(fields)
        _logger_for(os.path.join(root, rel)).info(json.dumps(rec, default=str))
    except Exception as exc:
        if stream == "gate":
            try:
                print(f"rookery: cannot write the gate log: {exc}", file=sys.stderr)
            except Exception:
                pass   # a closed stderr must not make logging raise


def _schema_current(conn: sqlite3.Connection) -> bool:
    """True when the sessions schema and the legacy session row already exist
    (checked with reads only, so it works while another connection writes)."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"rookery_sessions", "identities", "bindings", "inbox", "loop_counts"} <= have:
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}
    if "session_id" not in cols or not {c.split()[0] for c in rookery._INBOX_SCREENING_COLS} <= cols:
        return False
    return conn.execute(
        "SELECT 1 FROM rookery_sessions WHERE session_id='legacy'").fetchone() is not None


_UP_TABLES = ("up_agents", "worktrees", "claims", "ports")


def ensure_up_tables(conn: sqlite3.Connection) -> None:
    """Create the four rookery-up tables on an old database (additive, idempotent).
    Hooks never call this; only rookery up / finish / gc / regen / guard do."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(_UP_TABLES) <= have:
        rookery.init_db(conn)


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


_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def find_last_block(text):
    """Return the body of the last complete ```rookery fence in text, or None (D2).

    CommonMark fence rules: an opening fence is 3+ backticks or tildes, up to 3
    spaces of indent. A closing fence uses the same character, is at least as
    long, and has only whitespace after it. Every fence is tracked, not only
    rookery ones, so fence-looking lines inside another fence are content.
    """
    if not text:
        return None
    last = None
    fence = None      # (char, length, is_rookery) while inside a fence
    body: list = []
    for line in re.split(r"\r\n|\n|\r", text):
        m = _FENCE_LINE.match(line)
        if fence is None:
            if not m:
                continue
            run, info = m.group(1), m.group(2).strip()
            if run[0] == "`" and "`" in info:
                continue   # not a fence: backtick info string holds a backtick
            fence = (run[0], len(run), info == "rookery")
            body = []
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not m.group(2).strip():
            if fence[2]:
                last = "\n".join(body)
            fence = None
        elif fence[2]:
            body.append(line)
    return last   # an unclosed trailing fence is dropped


# --- Block validation (T017; research D12, FR-002 to FR-004) -----------------
# Size limits, research D12. Source: the screener envelope limit is ~2,050
# tokens for tev1 (register R16; okf/design/screener-chain.md). The envelope
# also holds scope cards and acceptance criteria; at about 4 characters per
# token, 4,096 bytes is about 1,000 tokens, half of the envelope. These are
# input-size limits on agent mail, not model token caps (constitution IV).
MAX_BLOCK_BYTES = 4096   # compact JSON: separators (",", ":"), sort_keys, ensure_ascii=False, UTF-8
MAX_SAY_CHARS = 1200
MAX_READ_CHARS = 256
MAX_THREAD_CHARS = 128

_FIELDS = {"to", "task", "kind", "say", "status", "thread", "read", "self_check", "artifacts"}
_REQUIRED = ("to", "task", "kind", "say", "status")
AGENT_KINDS = ("ask", "answer", "decline", "handoff", "status", "recruit")
SYSTEM_KINDS = ("veto", "takeover", "bounce")
_STATUSES = ("done", "in-progress")
_SELF_CHECK = {"task_complete": bool, "gap": str, "gap_in_my_scope": bool, "evidence": str}
_ARTIFACT = {"path": str, "note": str}


class _Reject(Exception):
    pass


def _reject_constant(name):
    raise ValueError(f"the constant {name} is not allowed")


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f'the key "{k}" appears twice')
        out[k] = v
    return out


def _show(value, limit=60) -> str:
    s = str(value)
    out, size = [], 0
    for ch in s:
        if size >= limit:
            out.append("...")
            break
        o = ord(ch)
        if o < 32 or 0x7F <= o <= 0x9F or ch in "\u2028\u2029" or 0xD800 <= o <= 0xDFFF:
            out.append({"\r": "\\r", "\n": "\\n", "\0": "\\0"}.get(
                ch, "\\x%02x" % o if o < 0x100 else "\\u%04x" % o))
        else:
            out.append(ch)
        size += len(out[-1])
    return "".join(out)


def _bad_string(obj):
    """First string (key or value) that is not UTF-8 encodable, else None. Iterative."""
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            try:
                x.encode("utf-8")
            except UnicodeEncodeError:
                return x
        elif isinstance(x, dict):
            for k, v in x.items():
                stack.append(k)
                stack.append(v)
        elif isinstance(x, list):
            stack.extend(x)
    return None


def _read_note(entry, root) -> str | None:
    """None if the read entry is fine, else the note for it."""
    if not isinstance(entry, str):
        return 'Each "read" entry must be a string path. Fix the entry, then send the block again.'
    if not entry or "\n" in entry or "\r" in entry or "\0" in entry:
        return (f'The read entry "{_show(entry)}" must be one line of text. '
                'Give one path per entry, then send the block again.')
    if len(entry) > MAX_READ_CHARS:
        return (f'The read entry "{_show(entry)}" has {len(entry)} characters. The limit is '
                f'{MAX_READ_CHARS}. Give a shorter path, then send the block again.')
    if ".." in re.split(r"[/\\]", entry):
        return (f'The read entry "{_show(entry)}" has a ".." part. Give the path without "..", '
                'then send the block again.')
    where = root if root else "the Rookery root"
    bad = (f'The read entry "{_show(entry)}" is not a path inside {where}. '
           f'Put the file inside {where} and give its path.')
    if not root:
        return bad if os.path.isabs(entry) else None
    try:
        real_root = os.path.realpath(root)
        target = os.path.realpath(entry if os.path.isabs(entry) else os.path.join(real_root, entry))
        return None if os.path.commonpath([real_root, target]) == real_root else bad
    except (ValueError, OSError):
        return bad


def _typed_note(field, value, spec) -> str | None:
    """Check an object against {name: type}; None if fine."""
    if not isinstance(value, dict):
        return f'The field "{field}" must be an object. Fix "{field}", then send the block again.'
    for k, v in value.items():
        if k not in spec:
            return (f'The field "{field}" has an unknown key "{_show(k)}". Remove it, '
                    'then send the block again.')
        if not isinstance(v, spec[k]):
            return (f'The key "{k}" in "{field}" must be a {spec[k].__name__}. '
                    'Fix it, then send the block again.')
    return None


def validate(raw, root):
    """Validate one block body (D12, FR-002 to FR-004).

    Returns (block, None) or (None, note). Never raises. The first rule that
    fails wins; each note names the rule and the next step (constitution VI).
    Compact size = len(json.dumps(obj, separators=(",", ":"), sort_keys=True,
    ensure_ascii=False).encode("utf-8")). '' is malformed, not "no block".
    """
    try:
        return _validate(raw, root)
    except Exception as exc:   # last line of defence: a hook must not crash
        return None, (f"The check of the block failed ({type(exc).__name__}). "
                      "Fix the block, then send it again.")


def _validate(raw, root):
    # 1. one JSON object
    try:
        if not isinstance(raw, str):
            raise ValueError("the block is not text")
        obj = json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_no_dupes)
    except (ValueError, RecursionError) as exc:
        return None, f"The block is not valid JSON: {_show(exc, 120)}. Fix the JSON, then send the block again."
    if not isinstance(obj, dict):
        return None, ("The block is not valid JSON: it must be one object, not "
                      f"{type(obj).__name__}. Fix the JSON, then send the block again.")
    if _bad_string(obj) is not None:
        return None, ("The block is not valid JSON: the text has an invalid character "
                      "(a lone surrogate). Fix the JSON, then send the block again.")
    # 2. size
    size = len(json.dumps(obj, separators=(",", ":"), sort_keys=True,
                          ensure_ascii=False).encode("utf-8", "surrogatepass"))
    if size > MAX_BLOCK_BYTES:
        return None, (f"The block has {size:,} bytes. The limit is {MAX_BLOCK_BYTES:,} bytes. "
                      'Put long text in a file and add its path to "read".')
    # 3. unknown field
    for k in obj:
        if k not in _FIELDS:
            return None, (f'The field "{_show(k)}" is not allowed in a block. Remove the field "{_show(k)}", '
                          "then send the block again.")
    # 4. required
    for k in _REQUIRED:
        if k not in obj:
            return None, (f'The field "{k}" is missing. Add "{k}"'
                          + (" with the name of an agent in your session" if k == "to" else "")
                          + ", then send the block again.")
    # 5. safe names
    for k in ("to", "task"):
        if not isinstance(obj[k], str):
            return None, f'The field "{k}" must be a string. Fix "{k}", then send the block again.'
        if not is_safe_name(obj[k]):
            return None, (f'The field "{k}" value "{_show(obj[k])}" is not a safe name. Use 1 to 64 '
                          "characters from A-Z a-z 0-9 . _ - (not . or ..), then send the block again.")
    # 6. kind
    if not isinstance(obj["kind"], str):
        return None, 'The field "kind" must be a string. Fix "kind", then send the block again.'
    if obj["kind"] not in AGENT_KINDS:
        tail = (" Use " + ", ".join(AGENT_KINDS[:-2]) + ", " + AGENT_KINDS[-2] + " or "
                + AGENT_KINDS[-1] + ", then send the block again.")
        if obj["kind"] in SYSTEM_KINDS:
            return None, f'The kind "{_show(obj["kind"])}" is for the system only.' + tail
        return None, f'The kind "{_show(obj["kind"])}" is not a kind.' + tail
    # 7. status
    if not isinstance(obj["status"], str):
        return None, 'The field "status" must be a string. Fix "status", then send the block again.'
    if obj["status"] not in _STATUSES:
        return None, (f'The status "{_show(obj["status"])}" is not allowed. Use done or in-progress '
                      "for status, then send the block again.")
    # 8. say
    if not isinstance(obj["say"], str):
        return None, 'The field "say" must be a string. Fix "say", then send the block again.'
    n = len(obj["say"])
    if not 1 <= n <= MAX_SAY_CHARS:
        return None, (f'The field "say" has {n:,} characters. It needs 1 to {MAX_SAY_CHARS:,}. '
                      'Shorten "say" or put long text in a file and add its path to "read", '
                      "then send the block again.")
    # 9. read
    if "read" in obj:
        if not isinstance(obj["read"], list):
            return None, 'The field "read" must be a list of paths. Fix "read", then send the block again.'
        for entry in obj["read"]:
            note = _read_note(entry, root)
            if note:
                return None, note
    # 10. other types
    if "thread" in obj and (not isinstance(obj["thread"], str) or len(obj["thread"]) > MAX_THREAD_CHARS):
        return None, (f'The field "thread" must be a string of at most {MAX_THREAD_CHARS} characters. '
                      'Fix "thread", then send the block again.')
    if "self_check" in obj:
        note = _typed_note("self_check", obj["self_check"], _SELF_CHECK)
        if note:
            return None, note
    if "artifacts" in obj:
        arts = obj["artifacts"]
        if not isinstance(arts, list):
            return None, 'The field "artifacts" must be a list. Fix "artifacts", then send the block again.'
        for a in arts:
            note = _typed_note("artifacts", a, _ARTIFACT)
            if note:
                return None, note
            if "path" not in a:
                return None, ('Each entry in "artifacts" needs a "path". Add "path", '
                              "then send the block again.")
    return obj, None


# --- Dispatch (T018; research D3, D4, D17, D22, FR-007, FR-035) --------------
import datetime
import fcntl
import hashlib
import secrets


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


MAX_RAW_BYTES = 8192   # stored/kept size of a malformed block's raw text (audit 6, fix 3)
MAX_RAW_TO_CHARS = 64


def _cap_raw(raw) -> str:
    """At most MAX_RAW_BYTES of raw (UTF-8 safe cut) plus a truncation marker."""
    text = raw if isinstance(raw, str) else str(raw)
    data = text.encode("utf-8", "replace")
    if len(data) <= MAX_RAW_BYTES:
        return text
    cut = data[:MAX_RAW_BYTES].decode("utf-8", "ignore")
    return f"{cut}\u2026[truncated, {len(data)} bytes]"


def _put_back(ctx, block, raw, failed: bool, reason=None) -> str | None:
    """Write the block to <workspace>/outbox[/failed]/<TASK-ID>-<ts>.json. Returns
    the path, or None if the file could not be written (logged by the caller)."""
    task = block.get("task") if isinstance(block, dict) else None
    name = task if is_safe_name(task) else "no-task"
    folder = os.path.join(workspace(ctx["root"], ctx["session"]["name"], ctx["identity"]["name"]),
                          "outbox", *(("failed",) if failed else ()))
    path = os.path.join(folder, f"{name}-{_stamp()}.json")
    if os.path.exists(path):
        path = os.path.join(folder, f"{name}-{_stamp()}-{secrets.token_hex(3)}.json")
    data = block if isinstance(block, dict) else {"raw": _cap_raw(raw)}
    write_json_atomic(path, dict(data, reason=reason) if reason else data)
    return path


def _finish(ctx, block, outcome, msg_id=None, reason=None, handoff_error=None):
    sender = ctx["identity"]
    extra = {"handoff_error": handoff_error} if handoff_error else {}
    log_event(ctx["root"], adapter=ctx.get("adapter"), session=ctx["session"]["session_id"],
              agent_id=sender["agent_id"], name=sender["name"],
              task=(block or {}).get("task") if isinstance(block, dict) else None,
              outcome=outcome, msg_id=msg_id, reason=reason, **extra)
    return {"outcome": outcome, "msg_id": msg_id, "reason": reason}


def _is_busy(exc) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg)


DEFAULT_LOOP_LIMIT = 10


def _pos_int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else None


def loop_limits():
    """(loop_limit, loop_pair_limit) from the Rookery config (FR-039). A missing, non-integer or
    < 1 loop_limit gives 10. A missing or bad loop_pair_limit gives 3 x loop_limit."""
    import rookery_identity     # local: rookery_identity imports this module
    try:
        cfg = rookery_identity.load_config(persist=False)
    except Exception:
        cfg = {}
    limit = _pos_int(cfg.get("loop_limit")) or DEFAULT_LOOP_LIMIT
    return limit, _pos_int(cfg.get("loop_pair_limit")) or 3 * limit


def loop_limit() -> int:
    return loop_limits()[0]


def reset_loop_counts(conn, agent_id) -> int:
    """A human prompt reached agent_id: zero every loop counter that names it. Returns the
    number of counters that were above zero. Writes nothing when there are none."""
    if conn.execute("SELECT 1 FROM loop_counts WHERE count>0 AND (a=? OR b=?) LIMIT 1",
                    (agent_id, agent_id)).fetchone() is None:
        return 0
    cur = conn.execute("UPDATE loop_counts SET count=0 WHERE count>0 AND (a=? OR b=?)",
                       (agent_id, agent_id))
    conn.commit()
    return cur.rowcount


def _top_level_failure(ctx, exc, block=None, raw=None):
    """A malformed ctx or an unexpected error escaped dispatch: log exactly one
    `send-failed` line (when a root is known), keep the block when the workspace
    can be determined, and return the result dict. Never raises."""
    reason = f"{type(exc).__name__}: {exc}"
    try:
        root = ctx["root"]
    except Exception:
        return {"outcome": "send-failed", "msg_id": None, "reason": reason}
    try:
        if block is not None or raw is not None:
            if (ctx.get("adapter")) in ("stop", "turn"):
                _put_back(ctx, block, raw, failed=False)
    except Exception as werr:
        reason += f" (could not keep the block: {werr})"
    fields = {}
    for key, get in (("adapter", lambda: ctx.get("adapter")),
                     ("session", lambda: ctx["session"]["session_id"]),
                     ("agent_id", lambda: ctx["identity"]["agent_id"]),
                     ("name", lambda: ctx["identity"]["name"]),
                     ("task", lambda: block.get("task") if isinstance(block, dict) else None)):
        try:
            fields[key] = get()
        except Exception:
            fields[key] = None
    log_event(root, outcome="send-failed", msg_id=None, reason=reason, **fields)
    return {"outcome": "send-failed", "msg_id": None, "reason": reason}


def dispatch_text(conn, text, ctx):
    """Find the last rookery block in text and dispatch it. Returns
    {"outcome", "msg_id", "reason"}; no block -> `no-block` (logged once).
    Never raises."""
    try:
        raw = find_last_block(text)
        if raw is None:
            return _finish(ctx, None, "no-block")
        return dispatch_block(conn, raw, ctx)
    except Exception as exc:
        return _top_level_failure(ctx, exc)


def dispatch_block(conn, raw, ctx):
    """Validate and route one block body (T018). Never raises."""
    try:
        return _dispatch_block(conn, raw, ctx)
    except Exception as exc:
        block = None
        try:
            block, _ = validate(raw, ctx["root"])
        except Exception:
            pass
        return _top_level_failure(ctx, exc, block, raw if block is None else None)


def _dispatch_block(conn, raw, ctx):
    import mail_gate    # local: mail_gate imports this module
    root, adapter = ctx["root"], ctx.get("adapter")
    keep = adapter in ("stop", "turn")   # the outbox adapter never puts a block back (D3)
    block, note = validate(raw, root)

    if os.environ.get("ROOKERY_URL"):
        reason = "ROOKERY_URL has a value. Remote sending does not work yet."
        if keep:
            try:
                _put_back(ctx, block, raw, failed=True)
            except Exception as exc:
                reason += f" (could not keep the block: {exc})"
        return _finish(ctx, block, "remote-unsupported", reason=reason)

    sender = ctx["identity"]
    sid = ctx["session"]["session_id"]
    herr = None   # a HANDOFF.md write failure is logged, never blocks mail

    def done(block, outcome, msg_id=None, reason=None):
        return _finish(ctx, block, outcome, msg_id, reason, handoff_error=herr)

    try:
        if block is None:
            to = None
            try:
                to = json.loads(raw).get("to")
            except Exception:
                pass
            recipient = to[:MAX_RAW_TO_CHARS] if isinstance(to, str) and to else "rookery"
            body = _cap_raw(raw)
            msg_id = mail_gate.quarantine_malformed(conn, ctx, recipient, body, None, "malformed", note)
            return done(None, "quarantined", msg_id, "malformed")

        if keep:   # FR-009: end-of-turn adapters only; before any DB work, outside any transaction
            try:
                write_handoff(workspace(root, ctx["session"]["name"], sender["name"]),
                              block, sender, ctx["session"])
            except Exception as hexc:
                herr = f"{type(hexc).__name__}: {hexc}"

        to, task = block["to"], block["task"]
        compact = json.dumps(block, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        if block["kind"] == "handoff" and block["status"] == "in-progress":
            # Stores no row (spec 001), so it is checked here, not through submit's pre_store.
            # A recipient problem still quarantines first, as before.
            if mail_gate._find_recipient(conn, sid, sender["agent_id"], to)[1] is None:
                return done(block, "handoff-pending",
                            reason="handoff is still in-progress; nothing sent")
        res = mail_gate.submit(conn, path="block", principal=(ctx["session"], sender),
                               raw_sender=sender["agent_id"], to=to, body=compact, topic=task,
                               task=task, asked_sender=None, root=root,
                               decided_by="turn-block-parser")
        outcome, msg_id = res["outcome"], res["msg_id"]
        if outcome == "quarantined":
            return done(block, "quarantined", msg_id, res["reason"])
        if outcome == "loop-held":
            if keep:     # park it: the next prompt notice names it. No bounce mail (it would feed the loop).
                try:
                    _put_back(ctx, block, raw, failed=True, reason="loop-held")
                except Exception as werr:
                    return done(block, "loop-held", msg_id, f"loop (could not keep the block: {werr})")
            return done(block, "loop-held", msg_id, "loop")
        if outcome == "sent":
            return done(block, "sent", msg_id)
        # db-busy, send-failed, refused: nothing stored; keep the block
        reason = res["reason"] or "database is busy"
        if keep:
            try:
                _put_back(ctx, block, raw, failed=False)
            except Exception as werr:
                reason += f" (could not keep the block: {werr})"
        return done(block, "db-busy" if outcome == "db-busy" else "send-failed", reason=reason)
    except Exception as exc:     # send/quarantine failed: log, keep the block, never raise
        try:
            conn.rollback()
        except Exception:
            pass
        outcome = "db-busy" if _is_busy(exc) else "send-failed"
        reason = str(exc)
        if keep:
            try:
                _put_back(ctx, block, raw, failed=False)
            except Exception as werr:
                reason += f" (could not keep the block: {werr})"
        return done(block, outcome, reason=reason)


def _one_line(value) -> str:
    """Collapse any whitespace run (incl. newlines) so a value stays on one list line."""
    return " ".join(str(value).replace("\x00", "").split())


_LINE_BREAKS = re.compile("\r\n|\r|\u2028|\u2029")


def _body(value) -> str:
    """Make free text safe for a HANDOFF.md body: drop NUL, normalise every line break
    to \\n, and backslash-escape a line that would read as a heading or a '---' fence."""
    text = _LINE_BREAKS.sub("\n", str(value).replace("\x00", ""))
    out = []
    for ln in text.split("\n"):
        t = ln.lstrip()
        out.append("\\" + ln if t.startswith("#") or t.startswith("---") else ln)
    return "\n".join(out)


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def write_handoff(ws: str, block: dict, identity: dict, session: dict) -> str:
    """Rewrite <ws>/runs/<task>/HANDOFF.md from a valid block (RQ-1.7, D11).

    Atomic: temp file in the same folder, fsync, os.replace. Returns the path.
    Body text (say, gap) has NUL removed, line breaks normalised, and any line that
    starts with '#' or '---' backslash-escaped, so it cannot forge a section or the
    frontmatter fences. thread, to and kind are collapsed to one line.
    """
    task = _check_name("task", block.get("task"))
    name = _check_name("agent", identity["name"])
    agent_id = _check_name("agent_id", str(identity["agent_id"]))
    session_name = _check_name("session", session["name"])
    status = block.get("status")
    if status not in ("done", "in-progress"):
        raise ValueError(f"bad handoff status: {status!r}")

    state = []
    for art in block.get("artifacts") or []:
        line = f"- {_one_line(art['path'])}"
        if art.get("note"):
            line += f" — {_one_line(art['note'])}"
        state.append(line)
    for path in block.get("read") or []:
        state.append(f"- {_one_line(path)} — read")
    gap = (block.get("self_check") or {}).get("gap")
    thread = block.get("thread")
    updated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    text = "\n".join([
        "---", "type: Handoff", f"task: {task}", f"agent: {name}", f"agent_id: {agent_id}",
        f"session: {session_name}", f"updated: {updated}", f"status: {status}", "---",
        "## Last thing I did", _body(block["say"]),
        "## State now (what exists, with paths)", "\n".join(state) if state else "Nothing stated.",
        "## What is left", _body(gap) if isinstance(gap, str) and gap.strip() else "Nothing stated.",
        "## Open threads",
        (f"{_one_line(thread)} with {_one_line(block['to'])} ({_one_line(block['kind'])})"
         if thread else "None."),
    ]) + "\n"

    d = os.path.join(ws, "runs", task)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "HANDOFF.md")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o666 & ~_umask())     # mkstemp makes 0600; match other files
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---- unread mail and failed-block notices (T036, research D7 / D23) ----------------------

MAX_FAILED_LISTED = 20   # newest failed blocks one notice pass will list


def unread(conn, agent_id) -> list:
    """Unread mail of agent_id (D7, D11): `mail_gate.unread` for its identity, so only its own
    session's mail plus `legacy` mail to its name or agent ID. Oldest first.
    Rows: id, sender (raw), sender_name, topic, created_at, session_id. No body.
    An agent ID with no identity has no session, so it reads nothing."""
    import mail_gate
    ident = conn.execute("SELECT agent_id, session_id, name FROM identities WHERE agent_id=?",
                         (agent_id,)).fetchone()
    if ident is None:
        return []
    return mail_gate.unread(conn, dict(ident))


def unread_summary(conn, agent_id) -> dict:
    """{"count", "senders", "tasks"}: de-duplicated, first-seen order; tasks = topics, None/empty skipped."""
    rows = unread(conn, agent_id)
    senders, tasks = [], []
    for r in rows:
        if r["sender_name"] not in senders:
            senders.append(r["sender_name"])
        t = r["topic"]
        if t and t not in tasks:
            tasks.append(t)
    return {"count": len(rows), "senders": senders, "tasks": tasks}


_DROP_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")
MAX_NOTICE_LISTED = 5     # senders / tasks named in one notice line
MAX_NOTICE_LEN = 400      # hard cap on one notice line
_NOTICE_TAIL = ". Read them with: rookery inbox"


def notice_field(s, cap=64) -> str:
    """One free-form value made safe to print or type into a pane: no control, format, surrogate or
    line/paragraph-separator characters (ESC, ^C, DEL, bidi marks, U+2028/9), whitespace runs
    collapsed to one space, stripped, cut at cap characters with an ellipsis."""
    s = "".join(c for c in str(s) if unicodedata.category(c) not in _DROP_CATEGORIES)
    s = " ".join(s.split())
    return s if len(s) <= cap else s[:cap - 1] + "\u2026"


def notice_list(items, limit=MAX_NOTICE_LISTED, cap=64) -> str:
    """Comma list of cleaned items; more than limit become 'a, b, ... and N more'."""
    items = list(items)
    shown = ", ".join(notice_field(x, cap) for x in items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def cap_notice(head, tail, cap=MAX_NOTICE_LEN) -> str:
    """head + tail, cut inside head (never the tail) when longer than cap."""
    if len(head) + len(tail) > cap:
        head = head[:max(cap - len(tail) - 1, 0)] + "\u2026"
    return head + tail


def format_unread_line(summary):
    """Contract line for the unread notice; None when there is no unread mail."""
    if not summary or not summary.get("count"):
        return None
    head = f"[rookery] {summary['count']} unread from {notice_list(summary['senders'])}"
    if summary.get("tasks"):
        head += f" about {notice_list(summary['tasks'])}"
    return cap_notice(head, _NOTICE_TAIL)


def format_failed_line(item) -> str:
    """Contract line for one failed block (D23). An interrupted block may well have been
    delivered, so its line warns against a blind resend instead of saying "fix the cause"."""
    if item.get("reason") == "interrupted":
        return (f"[rookery] Your block for {notice_field(item.get('task') or 'no-task')} may have "
                f"been sent before an interruption. It is kept in {notice_field(item['path'], 200)}. "
                "Check with the recipient or the mailroom before you send it again.")
    if item.get("reason") == "loop-held":
        return (f"[rookery] Your block for {notice_field(item.get('task') or 'no-task')} failed "
                f"(loop-held). Too many mails went between you and the other agent with no human "
                f"prompt. It is kept in {notice_field(item['path'], 200)}. "
                "Do not send it again until a human gives a prompt.")
    return (f"[rookery] Your block for {notice_field(item.get('task') or 'no-task')} failed "
            f"({notice_field(item.get('reason') or 'unknown')}). It is kept in "
            f"{notice_field(item['path'], 200)}. "
            "Fix the cause, then send the block again.")


def _seen_path(root, session_name, agent_name) -> str:
    return os.path.join(workspace(root, session_name, agent_name), "state", "failed-seen.json")


def _load_seen(path) -> set:
    try:
        with open(path) as fh:
            data = json.load(fh)
        return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def new_failed_blocks(root, session_name, agent_name) -> list:
    """Failed blocks of this identity that state/failed-seen.json does not name (D23). Read-only.
    Items: {"file", "path", "task", "reason"}. "file" is the seen key, prefixed by origin so the
    two folders cannot collide: "postmaster/<name>.result.json" or "outbox/<name>.json".
    Postmaster items: task/reason from the .result.json; path is the kept block next to it
    (the result name minus .result.json) if present, else the result file. Workspace items:
    task from the block JSON (else the file's TASK prefix), reason = its "reason" key if a
    string, else "remote-unsupported". Newest MAX_FAILED_LISTED by mtime, oldest first;
    unreadable or corrupt files are skipped; never raises."""
    try:
        _check_name("session", session_name)
        _check_name("agent", agent_name)
        ws = os.path.join(root, "agents", session_name, agent_name)
        seen = _load_seen(os.path.join(ws, "state", "failed-seen.json"))
        found = []   # (mtime, item)
        pdir = os.path.join(root, "postmaster", "outbox", session_name, agent_name, "failed")
        wdir = os.path.join(ws, "outbox", "failed")
        for d, prefix, suffix in ((pdir, "postmaster", ".result.json"), (wdir, "outbox", ".json")):
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for fn in names:
                if not fn.endswith(suffix) or fn.startswith(".tmp-"):
                    continue
                if prefix == "outbox" and fn.endswith(".result.json"):
                    continue
                key = f"{prefix}/{fn}"
                if key in seen:
                    continue
                path = os.path.join(d, fn)
                if os.path.islink(path):
                    continue
                data = _read_json(path)
                if not isinstance(data, dict):
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                task, reason = data.get("task"), data.get("reason")
                if prefix == "postmaster":
                    block = path[:-len(".result.json")]
                    shown = block if os.path.exists(block) else path
                    # The notice names the outcome (contract: "failed (send-failed)").
                    outcome = data.get("outcome")
                    if outcome == "handoff-pending":
                        continue     # filed under failed/ but nothing failed and nothing to fix
                    reason = outcome if isinstance(outcome, str) and outcome else (
                        reason if isinstance(reason, str) and reason else "failed")
                else:
                    shown = path
                    reason = reason if isinstance(reason, str) and reason else "remote-unsupported"
                    if not isinstance(task, str):
                        task = fn.rsplit("-", 1)[0] if "-" in fn else None
                if not isinstance(task, str) or not task:
                    task = None
                found.append((mtime, {"file": key, "path": os.path.abspath(shown),
                                      "task": task, "reason": reason}))
        found.sort(key=lambda p: (p[0], p[1]["file"]))
        return [it for _, it in found[-MAX_FAILED_LISTED:]]
    except Exception:
        return []


def mark_failed_seen(root, session_name, agent_name, files) -> None:
    """Add the "file" keys of printed failed blocks to state/failed-seen.json (atomic).
    The identity's own workspace, so the hook is its only writer."""
    path = _seen_path(root, session_name, agent_name)
    seen = _load_seen(path)
    seen.update(f for f in files if isinstance(f, str))
    folders = {"postmaster": os.path.join(root, "postmaster", "outbox", session_name, agent_name, "failed"),
               "outbox": os.path.join(root, "agents", session_name, agent_name, "outbox", "failed")}

    def alive(key):      # keys name a file in one of the two folders; drop the ones that are gone
        origin, _, fn = key.partition("/")
        return origin in folders and os.path.lexists(os.path.join(folders[origin], fn))

    seen = {k for k in seen if alive(k)}
    write_json_atomic(path, sorted(seen))


# ---- outbox scan (T046, research D3, D11) -------------------------------------------------

MAX_OUTBOX_BYTES = 1_048_576   # an outbox file larger than this is never read (failed: oversize)
_INTERRUPTED = "interrupted: check the mailroom before you send again"


def _outbox_dir(root, session, name) -> str:
    return os.path.join(root, "postmaster", "outbox", session, name)


def _clean(name) -> str:
    """A folder-safe stand-in for an unsafe name (kept under outbox/_unsafe/)."""
    s = re.sub(r"[^A-Za-z0-9._-]", "_", str(name))[:60]
    return "_" if s in ("", ".", "..") else s


_MAX_PARKED_NAME = 200   # bytes; keeps <name>.result.json (and a collision suffix) under NAME_MAX
_SCAN_SETTLE = 2.0       # seconds an outbox file must be untouched before it is claimed
_warned = set()          # (kind, root) already logged by this process


def _warn_once(kind, root, **fields):
    if (kind, root) not in _warned:
        _warned.add((kind, root))
        log_event(root, "postmaster", outcome=kind, **fields)


def _park(folder, subdir, fname, src, result) -> str | None:
    """Rename src into <folder>/<subdir>/<fname> (a short suffix on a name collision, never an
    overwrite) and write <dest>.result.json beside it. Returns the new path, or None when the
    rename failed (the file then stays where it was, so the next scan sees it again)."""
    dest_dir = os.path.join(folder, subdir)
    os.makedirs(dest_dir, exist_ok=True)
    if len(fname.encode("utf-8")) + len(".result.json") > _MAX_PARKED_NAME:
        head = fname.encode("utf-8")[:100].decode("utf-8", "ignore")
        fname = f"{head}-{hashlib.sha1(fname.encode('utf-8')).hexdigest()[:8]}.json"
    dest = os.path.join(dest_dir, fname)
    while os.path.lexists(dest) or os.path.lexists(dest + ".result.json"):
        dest = os.path.join(dest_dir, f"{fname}.{secrets.token_hex(3)}")
    os.rename(src, dest)
    try:
        write_json_atomic(dest + ".result.json", result)
    except Exception as exc:
        log_event(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(folder)))),
                  "postmaster", outcome="outbox-result-error", reason=str(exc), file=dest)
    return dest


def _regular_files(folder):
    """Names of the regular, non-hidden *.json files directly in folder (no symlinks)."""
    try:
        with os.scandir(folder) as it:
            return sorted(e.name for e in it
                          if e.name.endswith(".json") and not e.name.startswith(".")
                          and e.is_file(follow_symlinks=False))
    except OSError:
        return []


def _read_capped(path):
    """(text, None) for a regular file within MAX_OUTBOX_BYTES, else (None, reason)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        return None, f"unreadable: {exc}"
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None, "not a regular file"
        data = fh.read(MAX_OUTBOX_BYTES + 1)
    if len(data) > MAX_OUTBOX_BYTES:
        return None, "oversize"
    return data.decode("utf-8", "replace"), None


def _outbox_raw(text):
    """A put-back of a malformed block is {"raw": "<text>"}: dispatch that text. Anything
    else is dispatched as-is (dispatch_block validates it)."""
    try:
        obj = json.loads(text)
    except ValueError:
        return text
    if isinstance(obj, dict) and set(obj) == {"raw"} and isinstance(obj["raw"], str):
        return obj["raw"]
    return text


def _task_of(raw):
    try:
        t = json.loads(raw).get("task")
    except Exception:
        return None
    return t if is_safe_name(t) else None


def scan_outbox(conn, root, now=None) -> int:
    """One pass over <root>/agents/<session>/<name>/outbox/*.json (D3, D11). Writes only under
    <root>/postmaster/outbox/<session>/<name>/; the claim rename is the hand-over. Returns the
    number of files handled (claimed or failed). Never raises per file; never puts a file back.

    1. A file in sending/ (a crash mid-send) moves to failed/ as `interrupted`; never resent.
    2. Each outbox file is claimed with os.rename into sending/.
    3. dispatch_block(adapter="outbox") (no HANDOFF.md).
    4. -> sent/ when the outcome is `sent`; failed/ for any other outcome (quarantined,
       send-failed, db-busy, remote-unsupported, handoff-pending, ...). `<TASK-ID>-<file>` plus
       <that>.result.json {task, outcome, msg_id, reason}.
    5. One line per file in the postmaster log.
    """
    base = os.path.join(root, "postmaster", "outbox")
    try:
        os.makedirs(base, exist_ok=True)
        lock_fd = os.open(os.path.join(base, ".scan.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        log_event(root, "postmaster", outcome="outbox-error", reason=f"scan lock: {exc}")
        return 0
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _warn_once("scan-skipped-locked", root)
            return 0
        return _scan_locked(conn, root, base, time.time() if now is None else now)
    finally:
        os.close(lock_fd)      # closing releases the flock


def _scan_locked(conn, root, base, now) -> int:
    handled = 0
    # 1. restart rule
    for sess in _subdirs(base):
        for name in _subdirs(os.path.join(base, sess)):
            folder = os.path.join(base, sess, name)
            sending = os.path.join(folder, "sending")
            try:
                names = sorted(os.listdir(sending))
            except OSError:
                continue
            for fname in names:
                if fname.startswith("."):
                    continue
                src = os.path.join(sending, fname)
                try:
                    task = _task_of(_read_capped(src)[0] or "")
                    res = {"task": task, "outcome": "interrupted", "msg_id": None,
                           "reason": _INTERRUPTED, "note": _INTERRUPTED}
                    _park(folder, "failed", f"{task or 'no-task'}-{fname}", src, res)
                    log_event(root, "postmaster", scan="outbox", session=sess, name=name, task=task,
                              outcome="interrupted", reason=_INTERRUPTED, file=fname)
                    handled += 1
                except Exception as exc:
                    log_event(root, "postmaster", outcome="outbox-error", session=sess, name=name,
                              reason=f"{type(exc).__name__}: {exc}", file=fname)
    # 2-5. new files
    agents = os.path.join(root, "agents")
    for sess in _subdirs(agents):
        for name in _subdirs(os.path.join(agents, sess)):
            box = os.path.join(agents, sess, name, "outbox")
            if os.path.islink(box):
                _warn_once("outbox-symlink-skipped", root, session=sess, name=name)
                continue
            for fname in _regular_files(box):
                try:
                    if now - os.lstat(os.path.join(box, fname)).st_mtime < _SCAN_SETTLE:
                        continue         # maybe still being written; a later scan takes it
                    if _handle_file(conn, root, sess, name, os.path.join(box, fname), fname):
                        handled += 1
                except Exception as exc:
                    log_event(root, "postmaster", outcome="outbox-error", session=sess, name=name,
                              reason=f"{type(exc).__name__}: {exc}", file=fname)
    return handled


def _subdirs(path):
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it if e.is_dir(follow_symlinks=False))
    except OSError:
        return []


def _handle_file(conn, root, sess, name, path, fname) -> bool:
    safe = is_safe_name(sess) and is_safe_name(name)
    folder = (_outbox_dir(root, sess, name) if safe
              else _outbox_dir(root, "_unsafe", f"{_clean(sess)}__{_clean(name)}"))
    sdir = os.path.join(folder, "sending")
    os.makedirs(sdir, exist_ok=True)
    claimed = os.path.join(sdir, fname)
    if os.path.lexists(claimed):
        claimed = os.path.join(sdir, f"{fname}.{secrets.token_hex(3)}")
    try:
        os.rename(path, claimed)         # the claim: the file leaves the agent's workspace
    except OSError as exc:
        log_event(root, "postmaster", outcome="claim-failed", session=sess, name=name,
                  reason=str(exc), file=fname)
        return False

    def fail(reason, task=None, outcome=None):
        res = {"task": task, "outcome": outcome or reason, "msg_id": None, "reason": reason}
        _park(folder, "failed", f"{task or 'no-task'}-{fname}", claimed, res)
        log_event(root, "postmaster", scan="outbox", session=sess, name=name, task=task,
                  outcome=res["outcome"], reason=reason, file=fname)
        return True

    if not safe:
        return fail("unsafe-name")
    if os.path.islink(claimed) or not os.path.isfile(claimed):
        return fail("not a regular file", outcome="send-failed")
    sess_row = conn.execute("SELECT * FROM rookery_sessions WHERE name=?", (sess,)).fetchone()
    if sess_row is None:
        return fail("no-identity")
    if sess_row["state"] != "open":
        return fail("session-ended")
    ident = conn.execute("SELECT * FROM identities WHERE session_id=? AND name=?",
                         (sess_row["session_id"], name)).fetchone()
    if ident is None:
        return fail("no-identity")
    text, err = _read_capped(claimed)
    if err:
        return fail(err, outcome="oversize" if err == "oversize" else "send-failed")
    raw = _outbox_raw(text)
    ctx = {"root": root, "adapter": "outbox", "session": dict(sess_row), "identity": dict(ident)}
    res = dispatch_block(conn, raw, ctx)
    task = _task_of(raw)
    outcome = res.get("outcome")
    result = {"task": task, "outcome": outcome, "msg_id": res.get("msg_id"),
              "reason": res.get("reason")}
    _park(folder, "sent" if outcome == "sent" else "failed", f"{task or 'no-task'}-{fname}",
          claimed, result)
    log_event(root, "postmaster", scan="outbox", session=sess, agent_id=ident["agent_id"],
              name=name, task=task, outcome=outcome, msg_id=res.get("msg_id"),
              reason=res.get("reason"), file=fname)
    return True
