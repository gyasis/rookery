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
        return None, (f"The block could not be checked ({type(exc).__name__}). "
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
