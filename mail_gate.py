"""Rookery mail gate (spec 002, research D1).

Every local send goes through `submit()`. It checks the sender, finds the recipient, screens the
body, holds a loop, stores the mail and logs one `gate` record. `system_send()` is the one door
for Rookery's own mail. Standard library only. `rookery_identity` is imported inside functions
(it imports turn_block, which imports rookery).
"""
import difflib
import json
import re
import sqlite3

import rookery
import security
import turn_block as _tb
from turn_block import _is_busy, _show, loop_limit, loop_limits

SENDER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}(@[A-Za-z0-9._-]{1,64})?")
LEGACY = "legacy"
_INJECTION_NOTE = ("Rookery held this mail. The security policy found content that is not safe. "
                   "Remove that content, then send the mail again.")


def is_system_name(name) -> bool:
    """A system sender name in any case, alone or before `@<peer>` (`Rookery`, `postmaster@x`)."""
    return isinstance(name, str) and name.split("@", 1)[0].lower() in rookery.SYSTEM_SENDERS


def _begin(conn):
    """Take the write lock. As in turn_block, an open caller transaction is committed first."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


# --- moved from turn_block.py (spec 001 block path, D1) -------------------
def _quarantine_recipient(conn, to):
    """Recipient text for a quarantined row. A raw `to` that is a real agent ID
    or node ID would let that reader see the body, so it becomes `rookery`.
    Returns (recipient, masked)."""
    if not isinstance(to, str) or not to:
        return "rookery", False
    hit = conn.execute(
        "SELECT 1 FROM identities WHERE agent_id=? "
        "UNION ALL SELECT 1 FROM nodes WHERE node_id=? LIMIT 1", (to, to)).fetchone()
    return ("rookery", True) if hit is not None else (to, False)


def _hold_quarantine(conn, sender, sid, recipient, body, topic, reason, note, suggest, decided_by):
    """hold + quarantine (+ note) + bounce, inside the caller's transaction. Returns
    (msg_id, bounce_id)."""
    raw_to = recipient
    recipient, masked = _quarantine_recipient(conn, raw_to)
    if masked:
        note += f' (You addressed the block to "{_show(raw_to)}".)'
    msg_id = rookery.send(conn, sender, recipient, body, topic=topic, hold=True,
                          session_id=sid, commit=False)
    bounce_id = rookery.quarantine(conn, msg_id, reason, decided_by=decided_by, note=note,
                                   suggest=suggest, commit=False, bounce_limit=loop_limit())
    return msg_id, bounce_id


def quarantine_malformed(conn, ctx, recipient, body, topic, reason, note, suggest=None):
    """hold + quarantine (+ note) + bounce in ONE transaction (D4). BEGIN IMMEDIATE
    takes the write lock once, up front; any failure rolls everything back."""
    sid = ctx["session"]["session_id"]
    sender = ctx["identity"]["agent_id"]
    _begin(conn)
    try:
        msg_id, bounce_id = _hold_quarantine(conn, sender, sid, recipient, body,
                                             topic, reason, note, suggest, "turn-block-parser")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _log(ctx.get("root"), "block", (ctx["session"], ctx["identity"]), sender, None, recipient, sid,
         _result("quarantined", reason, msg_id=msg_id, bounce_id=bounce_id), "turn-block-parser")
    return msg_id


def _send_counted(conn, sender_id, recipient_id, body, task, sid, topic=None):
    """Send one agent mail. Counts it against two counters in the same transaction: one for the
    (task, pair) and one task-less for the pair (so a new task name does not evade, limit
    loop_pair_limit). At either limit the mail is stored `held` with reason `loop` instead, and no
    counter moves. Runs inside the caller's transaction. Returns (msg_id, held)."""
    limit, pair_limit = loop_limits()
    held = rookery.count_loop(conn, sid, [(task, sender_id, recipient_id, limit),
                                          ("~pair", sender_id, recipient_id, pair_limit)])
    msg_id = rookery.send(conn, sender_id, recipient_id, body, topic=topic, session_id=sid,
                          hold=True if held else None, commit=False)
    if held:
        conn.execute("UPDATE inbox SET reason='loop', note=? WHERE id=?",
                     (f"Held: too many mails on task {task} (or between these agents) with "
                      "no human prompt in between.", msg_id))
    return msg_id, held


# --- the gate ---------------------------------------------------------------
def _find_recipient(conn, sid, sender, to):
    """Checks 4 and 5. Returns (agent_id, None) or (None, (reason, note, suggest))."""
    row = conn.execute(
        "SELECT agent_id FROM identities WHERE session_id=? AND (name=? OR agent_id=?) "
        "ORDER BY (agent_id=?) DESC LIMIT 1", (sid, to, to, to)).fetchone()
    if row is not None:
        return row[0], None
    elsewhere = conn.execute(
        "SELECT 1 FROM identities WHERE name=? OR agent_id=? LIMIT 1", (to, to)).fetchone()
    if elsewhere is not None:
        note = (f'The agent "{_show(to)}" is in another session. Send to an agent '
                "in your own session, then send the block again.")
        return None, ("off-team", note, None)
    sender_name = conn.execute("SELECT name FROM identities WHERE agent_id=?", (sender,)).fetchone()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM identities WHERE session_id=? AND name<>? ORDER BY created_at, rowid",
        (sid, sender_name[0] if sender_name else sender))]
    suggest = difflib.get_close_matches(to, names, n=3)
    note = f'There is no agent named "{_show(to)}" in your session.'
    if suggest:
        note += " Did you mean " + " or ".join(f'"{_show(s)}"' for s in suggest) + "?"
    else:
        note += " Check the names of the agents in your session."
    note += " Fix \"to\", then send the block again."
    return None, ("wrong-recipient", note, suggest)


def _log(root, path, principal, sender, asked_sender, recipient, session, result, decided_by):
    _tb.log_event(root, "gate", path=path,
                  principal=principal[1]["agent_id"] if principal else None,
                  sender=sender, asked_sender=asked_sender, recipient=recipient, session=session,
                  outcome=result["outcome"], reason=result["reason"], warning=result["warning"],
                  msg_id=result["msg_id"], bounce_id=result["bounce_id"], decided_by=decided_by)


def _result(outcome, reason=None, warning=None, msg_id=None, bounce_id=None):
    return {"outcome": outcome, "reason": reason, "warning": warning,
            "msg_id": msg_id, "bounce_id": bounce_id}


def submit(conn, *, path, principal, raw_sender, to, body, topic="", task=None,
           asked_sender=None, root=None, decided_by="gate", pre_store=None):
    """The one door for mail from a local sender. Returns {"outcome", "reason", "warning",
    "msg_id", "bounce_id"}. Never raises for a mail problem. Order of checks: data-model.md."""
    if asked_sender:
        sender = asked_sender
    elif principal:
        sender = principal[1]["agent_id"]
    else:
        sender = raw_sender
    sid = principal[0]["session_id"] if principal else LEGACY
    warning = None if principal else "no-session"
    result = _result("send-failed")
    try:
        result = _submit(conn, sender, sid, warning, principal, to, body, topic, task, pre_store,
                         decided_by)
    except Exception as exc:        # nothing is stored: _submit rolled back
        result = (_result("db-busy", warning=warning) if _is_busy(exc)
                  else _result("send-failed", f"{type(exc).__name__}: {exc}", warning))
    _log(root, path, principal, sender, asked_sender, to, sid, result, decided_by)
    return result


def _submit(conn, sender, sid, warning, principal, to, body, topic, task, pre_store, decided_by):
    if not isinstance(sender, str):
        return _result("refused", "bad-input")
    if is_system_name(sender):                             # 1
        return _result("refused", "system-name")
    if SENDER_RE.fullmatch(sender) is None:                # 2
        return _result("refused", "bad-input")
    if not isinstance(to, str) or not to or not isinstance(body, str) or not body:
        return _result("refused", "bad-input")
    topic = topic or None
    # Screening reads no database row, so it runs before the write lock (a slow policy must not hold it).
    verdict = security.get_policy().inspect_inbound(sender, to, body)

    def quarantine(recipient, reason, note, suggest=None):
        msg_id, bounce_id = _hold_quarantine(conn, sender, sid, recipient, body, topic, reason,
                                             note, suggest, decided_by)
        return _result("quarantined", reason, warning, msg_id, bounce_id)

    _begin(conn)
    try:
        recipient = to
        if principal:                                      # 4, 5
            recipient, problem = _find_recipient(conn, sid, sender, to)
            if problem:
                out = quarantine(to, *problem)
                conn.commit()
                return out
        if not verdict:                                    # 6
            out = quarantine(to, "injection-suspect", _INJECTION_NOTE)
            conn.commit()
            return out
        if pre_store is not None:
            held = pre_store(conn.execute("SELECT * FROM identities WHERE agent_id=?",
                                          (recipient,)).fetchone())
            if held:
                msg_id = rookery.send(conn, sender, recipient, body, topic=topic, hold=True,
                                      session_id=sid, commit=False)
                conn.execute("UPDATE inbox SET reason=? WHERE id=?", (held[1], msg_id))
                conn.commit()
                return _result("sent", held[1], warning, msg_id)
        key = task or topic or "-"                         # 7, 8
        msg_id, loop_held = _send_counted(conn, sender, recipient, body, key, sid, topic)
        conn.commit()
        if loop_held:
            return _result("loop-held", "loop", warning, msg_id)
        return _result("sent", None, warning, msg_id)
    except BaseException:
        conn.rollback()
        raise


def system_send(conn, sender, recipient, body, *, topic=None, session_id, root=None) -> int:
    """Rookery's own mail (postmaster, relay, rookery). Raises ValueError for any other sender."""
    if sender not in rookery.SYSTEM_SENDERS:
        _log(root, "system", None, sender, None, recipient, session_id,
             _result("refused", "system-name"), "gate")
        raise ValueError(f"{sender!r} is not a system sender. Use submit().")
    try:
        msg_id = rookery.send(conn, sender, recipient, body, topic=topic, session_id=session_id)
    except Exception as exc:
        _log(root, "system", None, sender, None, recipient, session_id,
             _result("db-busy") if _is_busy(exc) else
             _result("send-failed", f"{type(exc).__name__}: {exc}"), "gate")
        raise
    _log(root, "system", None, sender, None, recipient, session_id,
         _result("sent", msg_id=msg_id), "gate")
    return msg_id


def principal_from_env(conn, env, root):
    """(session, identity) for the caller, or None. Only an `open` session whose identity already
    exists counts. Reads only: it never creates an identity (FR-011)."""
    import rookery_identity
    found = rookery_identity.resolve(conn, env, None, root, create=False)
    if found is None:
        return None
    session, identity, _ = found
    if identity is None or session["state"] != "open":
        return None
    return session, identity
