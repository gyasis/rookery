"""rookery_identity -- sessions, identities and the config file (research D17-D20).

Part 1: configuration, session functions and identity functions.
Part 2 (bottom of file): bindings_from, bind and resolve (D20).

Row shape: every function that returns a row returns a plain dict keyed by the
column names of the table (see data-model.md), whatever the connection's
row_factory. Timestamps are ISO-8601 UTC strings. Standard library only.
"""
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CONFIG_PATH = os.path.join(_HERE, "rookery_config.default.json")

_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ID_TRIES = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_name(name, what="name") -> str:
    """Return name if it matches [A-Za-z0-9._-]{1,64} and is not '.' or '..'.
    Raise ValueError otherwise."""
    if (not isinstance(name, str) or not _SAFE_NAME.fullmatch(name)
            or name in (".", "..")):
        raise ValueError(
            f"Bad {what} {name!r}. Use 1 to 64 characters from A-Z a-z 0-9 . _ - "
            "and not '.' or '..'.")
    return name


# --- config (D19) ----------------------------------------------------------
def _config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".rookery", "config.json")


def _read_default_config() -> str:
    """Read the default config from disk (repo) or, inside a zipapp, through
    the module loader (same approach as rookery._load_schema)."""
    try:
        with open(_DEFAULT_CONFIG_PATH) as fh:
            return fh.read()
    except OSError:
        loader = globals().get("__loader__")
        if loader is not None and hasattr(loader, "get_data"):
            return loader.get_data(_DEFAULT_CONFIG_PATH).decode("utf-8")
        raise


def load_config(persist=True) -> dict:
    """Return ~/.rookery/config.json as a dict. If the file does not exist, copy
    rookery_config.default.json there first (creating ~/.rookery); with
    persist=False return the packaged default instead and write nothing. HOME is
    read at call time. Raises ValueError (with the path) if the file is not valid JSON."""
    path = _config_path()
    if os.path.exists(path):
        with open(path) as fh:
            text = fh.read()
    else:
        text = _read_default_config()
        if persist:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w") as fh:
                fh.write(text)
            os.replace(tmp, path)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Config file {path} is not valid JSON: {exc}") from exc


# --- helpers ---------------------------------------------------------------
def _one(conn, sql, params=()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return {d[0]: row[i] for i, d in enumerate(cur.description)}


def _all(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _parse_bind(b):
    """Accept (kind, value) or 'kind:value'."""
    if isinstance(b, str):
        kind, sep, value = b.partition(":")
        if not sep or not kind or not value:
            raise ValueError(f"Bad binding {b!r}. Use KIND:VALUE.")
        return kind, value
    kind, value = b
    return str(kind), str(value)


# --- sessions (D18) --------------------------------------------------------
def new_session(conn, name=None, binds=()) -> dict:
    """Create an open session; return its row dict
    {session_id, name, state, created_at, ended_at}.

    name=None -> the name is the session ID. A name is unique forever, including
    names of ended sessions (ValueError). binds: iterable of (kind, value) or
    'kind:value' strings; each is stored as a session binding."""
    if name is not None:
        safe_name(name, "session name")
        if _one(conn, "SELECT 1 FROM rookery_sessions WHERE name=?", (name,)):
            raise ValueError(
                f"The session name {name!r} is already used. Names are never reused, "
                "even after a session ends. Pick another name.")
    pairs = [_parse_bind(b) for b in binds]
    created = _now_iso()
    for _ in range(_ID_TRIES):
        sid = "rs-" + secrets.token_hex(4)
        if _one(conn, "SELECT 1 FROM rookery_sessions WHERE session_id=?", (sid,)):
            continue
        try:
            conn.execute(
                "INSERT INTO rookery_sessions(session_id, name, state, created_at) "
                "VALUES(?,?,?,?)", (sid, name or sid, "open", created))
        except sqlite3.IntegrityError:
            if name is not None and _one(
                    conn, "SELECT 1 FROM rookery_sessions WHERE name=?", (name,)):
                conn.rollback()
                raise ValueError(f"The session name {name!r} is already used.")
            continue  # ID or auto-name clash: try a new ID
        try:
            for kind, value in pairs:
                conn.execute(
                    "INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, "
                    "created_at) VALUES(?,?,?,?,?,?)", (sid, "session", sid, kind, value, created))
        except sqlite3.Error:
            conn.rollback()
            raise
        conn.commit()
        return get_session(conn, sid)
    raise RuntimeError("Could not generate a unique session ID after "
                       f"{_ID_TRIES} tries.")


def list_sessions(conn) -> list:
    """All sessions (open and ended, including 'legacy'), oldest first, as a list
    of row dicts."""
    return _all(conn, "SELECT * FROM rookery_sessions ORDER BY created_at, rowid")


def get_session(conn, name_or_id):
    """Row dict for a session found by session_id, else by name. None if absent."""
    if not isinstance(name_or_id, str):
        return None
    return (_one(conn, "SELECT * FROM rookery_sessions WHERE session_id=?", (name_or_id,))
            or _one(conn, "SELECT * FROM rookery_sessions WHERE name=?", (name_or_id,)))


def end_session(conn, name_or_id) -> dict:
    """Set state=ended and ended_at; delete nothing. Return the updated row dict.
    Ending an ended session keeps its first ended_at. ValueError if not found."""
    s = get_session(conn, name_or_id)
    if s is None:
        raise ValueError(f"No Rookery session {name_or_id!r}.")
    if s["state"] != "ended":
        conn.execute("UPDATE rookery_sessions SET state='ended', ended_at=? "
                     "WHERE session_id=?", (_now_iso(), s["session_id"]))
        conn.commit()
    return get_session(conn, s["session_id"])


# --- identities (D17, D18) -------------------------------------------------
def new_identity(conn, session_id, name, previous_id=None) -> dict:
    """Create an identity in a session; return its row dict
    {agent_id, session_id, name, previous_id, created_at}.

    session_id is a session ID (a name is also accepted and resolved). ValueError
    for a bad name, an unknown session, or a name already used in that session."""
    try:
        row = _new_identity(conn, session_id, name, previous_id)
    except ValueError:
        conn.rollback()
        raise
    conn.commit()
    return row


def _new_identity(conn, session_id, name, previous_id=None) -> dict:
    """new_identity without commit or rollback (resolve runs it inside its own
    transaction)."""
    safe_name(name, "agent name")
    s = get_session(conn, session_id)
    if s is None:
        raise ValueError(f"No Rookery session {session_id!r}.")
    sid = s["session_id"]
    if _one(conn, "SELECT 1 FROM identities WHERE session_id=? AND name=?", (sid, name)):
        raise ValueError(f"The agent name {name!r} is already used in session "
                         f"{s['name']!r}.")
    created = _now_iso()
    for _ in range(_ID_TRIES):
        aid = "ag-" + secrets.token_hex(5)
        if _one(conn, "SELECT 1 FROM identities WHERE agent_id=?", (aid,)):
            continue
        try:
            conn.execute(
                "INSERT INTO identities(agent_id, session_id, name, previous_id, "
                "created_at) VALUES(?,?,?,?,?)", (aid, sid, name, previous_id, created))
        except sqlite3.IntegrityError:
            if _one(conn, "SELECT 1 FROM identities WHERE session_id=? AND name=?",
                    (sid, name)):
                raise ValueError(f"The agent name {name!r} is already used in "
                                 f"session {s['name']!r}.")
            continue
        return get_identity(conn, aid)
    raise RuntimeError(f"Could not generate a unique agent ID after {_ID_TRIES} tries.")


def list_identities(conn, session_id) -> list:
    """Identities of a session (ID or name), oldest first, as row dicts. Empty
    list for an unknown session."""
    s = get_session(conn, session_id)
    if s is None:
        return []
    return _all(conn, "SELECT * FROM identities WHERE session_id=? "
                "ORDER BY created_at, rowid", (s["session_id"],))


def get_identity(conn, id_or_name, session_id=None):
    """Row dict for an identity, or None. An agent ID matches anywhere (and, if
    session_id is given, only in that session). A name needs session_id (ID or
    name); without it the name must match exactly one identity, else ValueError
    (ambiguous)."""
    if not isinstance(id_or_name, str):
        return None
    sid = None
    if session_id is not None:
        s = get_session(conn, session_id)
        if s is None:
            return None
        sid = s["session_id"]
    if sid is None:
        row = _one(conn, "SELECT * FROM identities WHERE agent_id=?", (id_or_name,))
        if row:
            return row
        rows = _all(conn, "SELECT * FROM identities WHERE name=?", (id_or_name,))
        if len(rows) > 1:
            raise ValueError(f"The name {id_or_name!r} is in {len(rows)} sessions. "
                             "Pass session_id.")
        return rows[0] if rows else None
    return (_one(conn, "SELECT * FROM identities WHERE agent_id=? AND session_id=?",
                 (id_or_name, sid))
            or _one(conn, "SELECT * FROM identities WHERE name=? AND session_id=?",
                    (id_or_name, sid)))


# --- part 2: bindings and resolution (D20, FR-026, FR-027) -----------------
_AGENT_ID = re.compile(r"ag-[0-9a-f]{10}")


def _log(root, **fields):
    """Log through turn_block.log_event (imported late: turn_block imports rookery)."""
    import turn_block
    turn_block.log_event(root, stream="turn-block", **fields)


def bindings_from(env, hook_input, config) -> list:
    """Presented identity bindings as a list of (kind, value) tuples: one per
    non-empty variable of config['identity_binding_sources'] (config order), then
    ('claude-session', hook_input['session_id']) when the hook input has one."""
    out = []
    for var, kind in (config.get("identity_binding_sources") or {}).items():
        val = env.get(var)
        if val:
            out.append((kind, str(val)))
    sid = (hook_input or {}).get("session_id")
    if sid:
        out.append(("claude-session", str(sid)))
    return out


def _holder(conn, session_id, kind, value):
    r = _one(conn, "SELECT owner_id FROM bindings WHERE session_id=? AND "
             "owner_type='identity' AND kind=? AND value=?", (session_id, kind, value))
    return r["owner_id"] if r else None


def bind(conn, session_id, agent_id, kind, value, root=None) -> str:
    """Give (kind, value) to agent_id inside session_id. A value held by another
    identity of that session moves to agent_id and 'binding-moved' is logged.
    Returns 'bound', 'moved' or 'unchanged'. Commits. root: log root (no log if None)."""
    result, holder = _bind(conn, session_id, agent_id, kind, value)
    conn.commit()
    _log_bind(root, result, holder, session_id, agent_id, kind, value)
    return result


def _bind(conn, session_id, agent_id, kind, value):
    """bind without commit or logging. Returns (result, previous_holder)."""
    now = _now_iso()
    holder = _holder(conn, session_id, kind, value)
    if holder == agent_id:
        return "unchanged", holder
    if holder is None:
        conn.execute("INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, "
                     "created_at) VALUES(?,?,?,?,?,?)",
                     (session_id, "identity", agent_id, kind, value, now))
        return "bound", None
    conn.execute("UPDATE bindings SET owner_id=?, created_at=? WHERE session_id=? AND "
                 "owner_type='identity' AND kind=? AND value=?",
                 (agent_id, now, session_id, kind, value))
    return "moved", holder


def _log_bind(root, result, holder, session_id, agent_id, kind, value):
    if not root:
        return
    if result == "bound":
        _log(root, event="bound", session=session_id, agent_id=agent_id,
             kind=kind, value=value)
    elif result == "moved":
        _log(root, event="binding-moved", session=session_id, agent_id=agent_id,
             from_agent_id=holder, kind=kind, value=value)


def find_session(conn, env, config):
    """Read-only session lookup: ROOKERY_SESSION (ID or name), else the first
    session binding of config['session_binding_sources'] present in env. Returns
    the session row dict or None. Logs nothing and writes nothing."""
    ref = env.get("ROOKERY_SESSION")
    if ref:
        return get_session(conn, ref)
    for var, kind in (config.get("session_binding_sources") or {}).items():
        val = env.get(var)
        if not val:
            continue
        r = _one(conn, "SELECT s.* FROM bindings b JOIN rookery_sessions s ON "
                 "s.session_id=b.session_id WHERE b.owner_type='session' AND b.kind=? "
                 "AND b.value=? ORDER BY (s.state='open') DESC, s.created_at DESC, "
                 "s.rowid DESC", (kind, str(val)))
        if r:
            return r
    return None


def resolve(conn, env, hook_input, root, create=True):
    """Resolve who is calling (D20). Returns None (and logs 'no-session') when no
    session is found; else (session_row, identity_row, bindings) where bindings is
    the presented [(kind, value)] list. identity_row is None only when create=False
    and no identity exists. create=False writes nothing: no identity, no binding
    insert, no move. With create=True the writes run in one BEGIN IMMEDIATE
    transaction, so concurrent callers converge on the same rows. Logged events:
    no-session, unknown-session, bad-node, unknown-node-id, created, bound,
    binding-moved."""
    config = load_config(persist=False)
    session = find_session(conn, env, config)
    if session is None:
        if env.get("ROOKERY_SESSION"):
            _log(root, event="unknown-session",
                 reason=f"ROOKERY_SESSION={env['ROOKERY_SESSION']!r} not found")
        _log(root, event="no-session", reason="no ROOKERY_SESSION and no session binding matched")
        return None
    if not create:
        return _resolve_identity(conn, env, hook_input, root, config, session, False)
    conn.commit()                       # end any implicit transaction; BEGIN needs none open
    conn.execute("BEGIN IMMEDIATE")     # busy_timeout applies; check + insert are atomic
    try:
        out = _resolve_identity(conn, env, hook_input, root, config, session, True)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return out


def _resolve_identity(conn, env, hook_input, root, config, session, create):
    sid = session["session_id"]
    presented = bindings_from(env, hook_input, config)
    identity = None

    node = env.get("ROOKERY_NODE")
    if node:
        if _AGENT_ID.fullmatch(node):
            identity = get_identity(conn, node, sid)
            if identity is None:
                _log(root, event="unknown-node-id", session=sid, agent_id=node,
                     reason="ROOKERY_NODE agent ID not in this session; ignored")
        else:
            try:
                safe_name(node, "ROOKERY_NODE")
            except ValueError as exc:
                _log(root, event="bad-node", session=sid, name=node, reason=str(exc))
            else:
                identity = get_identity(conn, node, sid)
                if identity is None and create:
                    try:
                        identity = _new_identity(conn, sid, node)
                    except (ValueError, sqlite3.IntegrityError):
                        identity = get_identity(conn, node, sid)  # lost a race: use the winner
                        if identity is None:
                            raise
                    else:
                        _log(root, event="created", session=sid,
                             agent_id=identity["agent_id"], name=node)

    if identity is None:
        for kind, value in presented:
            holder = _holder(conn, sid, kind, value)
            if holder:
                identity = get_identity(conn, holder, sid)
                break
    if identity is None and create:
        harness = (hook_input or {}).get("harness") or "claude"
        harness = re.sub(r"[^A-Za-z0-9._-]", "", str(harness))[:50] or "claude"
        for _ in range(_ID_TRIES):
            try:
                identity = _new_identity(conn, sid, f"{harness}-{secrets.token_hex(2)}")
                break
            except ValueError:
                continue  # 4-hex name clash in this session: try another
        if identity is None:
            raise RuntimeError("Could not generate a unique default agent name.")
        _log(root, event="created", session=sid, agent_id=identity["agent_id"],
             name=identity["name"])

    if identity is not None and create:
        for kind, value in presented:
            result, holder = _bind(conn, sid, identity["agent_id"], kind, value)
            _log_bind(root, result, holder, sid, identity["agent_id"], kind, value)
    return session, identity, presented
