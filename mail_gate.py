"""Rookery mail gate (spec 002, research D1).

Every local send goes through `submit()`. It checks the sender, finds the recipient, screens the
body, holds a loop, stores the mail and logs one `gate` record. `system_send()` is the one door
for Rookery's own mail. Standard library only. `rookery_identity` is imported inside functions
(it imports turn_block, which imports rookery).
"""
import difflib
import json
import os
import re
import sqlite3

import rookery
import screener_config
import screener_t0
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


def _screen_pre_store(conn, get_cfg):
    """The default `pre_store` hook: a live T1 check in review mode `pre` holds the mail as `screening` (D13).
    A configuration that cannot be read holds nothing: the mail must still be stored."""
    def hook(recipient):
        cfg = get_cfg()
        return None if cfg is None else screener_t0.pre_hold(conn, recipient, cfg)
    return hook


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


def _hold_quarantine(conn, sender, sid, recipient, body, topic, reason, note, suggest, decided_by,
                     thread=None):
    """hold + quarantine (+ note) + bounce, inside the caller's transaction. Also writes the
    `screen_mail` row `decided` (a T0 quarantine at store: the screener skips it, FR-005, D9).
    Returns (msg_id, bounce_id)."""
    raw_to = recipient
    recipient, masked = _quarantine_recipient(conn, raw_to)
    if masked:
        note += f' (You addressed the block to "{_show(raw_to)}".)'
    msg_id = rookery.send(conn, sender, recipient, body, topic=topic, hold=True,
                          session_id=sid, commit=False, thread=thread)
    bounce_id = rookery.quarantine(conn, msg_id, reason, decided_by=decided_by, note=note,
                                   suggest=suggest, commit=False, bounce_limit=loop_limit())
    conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, final_label, final_by, decided_at) "
                 "VALUES(?,?,?,?,?,?)", (msg_id, sid, "decided", reason, decided_by, rookery.now()))
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


def _bounce(conn, sid, sender, topic, text):
    """A plain-text bounce from `rookery` to the sender, in the caller's transaction. A bounce loop is held (FR-039).
    Returns the bounce ID."""
    held = rookery.count_loop(conn, sid, [("~bounce", "rookery", sender, loop_limit())])
    bid = rookery.send(conn, "rookery", sender, text, topic=topic, session_id=sid, hold=True if held else None,
                       commit=False)
    if held:
        conn.execute("UPDATE inbox SET reason='loop', note=? WHERE id=?",
                     ("Held: too many bounces to this agent with no human prompt in between.", bid))
    return bid


def _send_counted(conn, sender_id, recipient_id, body, task, sid, topic=None, thread=None, hold_reason=None):
    """Send one agent mail. Counts it against two counters in the same transaction: one for the
    (task, pair) and one task-less for the pair (so a new task name does not evade, limit
    loop_pair_limit). At either limit the mail is stored `held` with reason `loop` instead, and no
    counter moves. `hold_reason` (a pre-store hold, such as `screening`) holds the mail and is counted too;
    the reason `loop` takes precedence. Runs inside the caller's transaction. Returns (msg_id, loop_held)."""
    limit, pair_limit = loop_limits()
    held = rookery.count_loop(conn, sid, [(task, sender_id, recipient_id, limit),
                                          ("~pair", sender_id, recipient_id, pair_limit)])
    msg_id = rookery.send(conn, sender_id, recipient_id, body, topic=topic, session_id=sid,
                          hold=True if held or hold_reason else None, commit=False, thread=thread)
    if held:
        conn.execute("UPDATE inbox SET reason='loop', note=? WHERE id=?",
                     (f"Held: too many mails on task {task} (or between these agents) with "
                      "no human prompt in between.", msg_id))
    elif hold_reason:
        conn.execute("UPDATE inbox SET reason=? WHERE id=?", (hold_reason, msg_id))
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


def _wake(conn, msg_ids, root):
    """Wake the recipients of the pending rows among msg_ids (feature 005). after_pending skips a row that
    is not pending (held, quarantined) and never raises; this guard makes a send safe even so."""
    ids = [i for i in msg_ids if i]
    if not ids:
        return
    try:
        import rookery_wake      # inside: postmaster imports mail_gate
        rookery_wake.after_pending(conn, ids, log_root=root)
    except Exception:
        pass


def _screen_event(conn, msg_id, root):
    """Spawn one detached `rookery screen run --session S --once` after the commit of a stored mail that
    needs a decision (pending, or held for screening) when screening is on for its session (feature 005
    T015, FR-009). Never raises and never changes the send result."""
    if not msg_id or os.environ.get("ROOKERY_WAKE") == "off":   # the operator kill switch covers every event spawn
        return
    try:
        import rookery_identity, rookery_wake, screener_config   # inside: import cycles and startup cost
        row = conn.execute("SELECT status, reason, session_id FROM inbox WHERE id=?", (msg_id,)).fetchone()
        if row is None:
            return
        status, reason, sid = row[0], row[1], row[2]
        if not (status == "pending" or (status == "held" and reason == "screening")):
            return
        if not sid or sid == LEGACY:
            return
        sess = conn.execute("SELECT state FROM rookery_sessions WHERE session_id=?", (sid,)).fetchone()
        if sess is None or sess[0] != "open":
            return
        # a mail held for screening exists only because a person set t1 live: not screening it leaves it stuck
        if status == "held" or screener_config.screening_on(conn, rookery_identity.load_config(persist=False), sid):
            rookery_wake.spawn(["screen", "run", "--session", sid, "--once"], log_root=root)
    except Exception:
        pass


def _result(outcome, reason=None, warning=None, msg_id=None, bounce_id=None):
    return {"outcome": outcome, "reason": reason, "warning": warning,
            "msg_id": msg_id, "bounce_id": bounce_id}


def submit(conn, *, path, principal, raw_sender, to, body, topic="", task=None,
           asked_sender=None, root=None, decided_by="gate", pre_store=None, thread=None, block=None):
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
                         decided_by, thread, block)
    except Exception as exc:        # nothing is stored: _submit rolled back
        result = (_result("db-busy", warning=warning) if _is_busy(exc)
                  else _result("send-failed", f"{type(exc).__name__}: {exc}", warning))
    _log(root, path, principal, sender, asked_sender, to, sid, result, decided_by)
    _wake(conn, [result.get("msg_id"), result.get("bounce_id")], root)   # after the commit; the send is already done
    _screen_event(conn, result.get("msg_id"), root)
    return result


def _submit(conn, sender, sid, warning, principal, to, body, topic, task, pre_store, decided_by,
            thread=None, block=None):
    if not isinstance(sender, str):
        return _result("refused", "bad-input")
    if is_system_name(sender):                             # 1
        return _result("refused", "system-name")
    if SENDER_RE.fullmatch(sender) is None:                # 2
        return _result("refused", "bad-input")
    if not isinstance(to, str) or not to or not isinstance(body, str) or not body:
        return _result("refused", "bad-input")
    topic = topic or None
    cfg_box = []

    def get_cfg():   # one read for this send, shared by pre_hold and the thread check; None when unreadable or invalid
        if not cfg_box:
            try:
                cfg_box.append(screener_config.load())
            except (ValueError, OSError) as err:
                cfg_box.append(None)
                _tb.log_event(None, "gate", event="config-unreadable", error=str(err))
                return None
            try:
                screener_config.check(cfg_box[0])   # a value that the screener refuses must not drive the gate
            except ValueError as err:
                cfg_box[0] = None
                _tb.log_event(None, "gate", event="config-invalid", error=str(err))
        return cfg_box[0]
    # Screening reads no database row, so it runs before the write lock (a slow policy must not hold it).
    verdict = security.get_policy().inspect_inbound(sender, to, body)

    def quarantine(recipient, reason, note, suggest=None):
        msg_id, bounce_id = _hold_quarantine(conn, sender, sid, recipient, body, topic, reason,
                                             note, suggest, decided_by, thread)
        return _result("quarantined", reason, warning, msg_id, bounce_id)

    cfg = get_cfg()    # the T0 injection scan reads no row, so it also runs before the write lock
    imode = cfg["checks"].get("injection", "off") if cfg else "off"
    hit = screener_t0.injection_scan(body) if imode != "off" else None

    _begin(conn)
    try:
        recipient = to
        if principal and "@" not in to:                    # 4, 5 (node@peer is a remote address)
            recipient, problem = _find_recipient(conn, sid, sender, to)
            if problem:
                out = quarantine(to, *problem)
                gmode = cfg["checks"].get("gates", "off") if cfg else "off"
                if gmode != "off":                         # G5: a B1 failure, the mail is already quarantined
                    screener_t0.gates_record(conn, sid, out["msg_id"], screener_t0.gate_anomalies(block, False, True),
                                             gmode)
                conn.commit()
                return out
        if not verdict:                                    # 6
            out = quarantine(to, "injection-suspect", _INJECTION_NOTE)
            conn.commit()
            return out
        if hit and imode == "live":                        # 6b: T0 injection set (D18)
            out = quarantine(to, "injection-suspect", _INJECTION_NOTE)
            screener_t0.injection_record(conn, sid, out["msg_id"], hit, "live")
            conn.commit()
            return out
        if pre_store is None:
            pre_store = _screen_pre_store(conn, get_cfg)
        held = None
        if pre_store is not None:
            held = pre_store(conn.execute("SELECT * FROM identities WHERE agent_id=?",
                                          (recipient,)).fetchone())
        tcheck = screener_t0.thread_check(conn, sid, thread, sender, held is not None, get_cfg()) if thread else None
        if tcheck == "denied":                             # B3 live (D16): not stored, a bounce, no wake
            out = _result("refused", "thread-closed", warning,
                          bounce_id=_bounce(conn, sid, sender, topic, screener_t0.THREAD_BOUNCE.format(thread=_show(thread))))
            conn.commit()
            return out
        key = task or topic or "-"                         # 7, 8
        digest = budget = dup = None
        reason = held[1] if held else None
        if block and block.get("kind") == "ask" and isinstance(block.get("say"), str):   # B2 (D17)
            digest = screener_t0.ask_hash(block["say"])
            budget, dup = screener_t0.ask_check(conn, sid, sender, recipient, key, digest, get_cfg())
            if budget and cfg["checks"]["budget"] == "live":     # loop > budget > duplicate > screening
                reason = "budget"
            elif dup and cfg["checks"]["duplicate"] == "live":
                reason = "duplicate"
        gmode = cfg["checks"].get("gates", "off") if cfg else "off"
        anomalies = screener_t0.gate_anomalies(block, budget is not None, False) if gmode != "off" else []
        if anomalies and gmode == "live" and reason not in ("budget", "duplicate"):
            reason = "gates"                               # loop > budget > duplicate > gates > screening
        msg_id, loop_held = _send_counted(conn, sender, recipient, body, key, sid, topic, thread, hold_reason=reason)
        bounce_id = None
        if digest:
            screener_t0.ask_stored(conn, sid, msg_id, digest, budget, dup, cfg, loop_held)
            if not loop_held and reason == "budget":
                bounce_id = _bounce(conn, sid, sender, topic, screener_t0.BUDGET_BOUNCE.format(
                    task=_show(key), limit=budget[1]))
            elif not loop_held and reason == "duplicate":
                bounce_id = _bounce(conn, sid, sender, topic, screener_t0.DUPLICATE_BOUNCE.format(
                    recipient=_show(to), time=screener_t0.duplicate_time(dup)))
        if tcheck:
            screener_t0.thread_stored(conn, sid, thread, msg_id, tcheck, get_cfg())
        gated = bool(anomalies) and not loop_held          # a loop hold drops the gate records, as for B2
        if gated:
            parked = reason == "gates"
            screener_t0.gates_record(conn, sid, msg_id, anomalies, gmode, "escalate" if parked else "none")
            if parked:                                     # same transaction as the store; the notice follows the commit
                why = "gates found " + ", ".join(a[0] for a in anomalies)
                rookery._decide(conn, msg_id, "held", "escalated", "escalate", why, "t0:gates", commit=False)
        if hit:                                            # shadow: record and deliver
            screener_t0.injection_record(conn, sid, msg_id, hit, "shadow")
        conn.commit()
        if gated and parked:
            try:
                screener_t0.notify_escalated(conn, msg_id, why)
            except Exception as err:                       # the escalation stands; a failed notice is logged
                conn.rollback()
                _tb.log_event(None, "gate", event="notice-failed", msg_id=msg_id, error=str(err))
        if loop_held:
            return _result("loop-held", "loop", warning, msg_id)
        return _result("sent", reason, warning, msg_id, bounce_id)
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
    _wake(conn, [msg_id], root)
    return msg_id


# --- reads (FR-015, FR-016, D11) ------------------------------------------
def _unread_rows(conn, sql, params, with_body):
    out = []
    for r in conn.execute(sql, params).fetchall():
        row = {"id": r["id"], "sender": r["sender"],
               "sender_name": r["sender_name"] or r["sender"], "topic": r["topic"],
               "created_at": r["created_at"], "session_id": r["session_id"]}
        if with_body:
            row["body"] = r["body"]
        out.append(row)
    return out


def unread(conn, identity, *, with_body=False) -> list:
    """Pending mail of the identity's session to its agent ID, plus pending `legacy` mail to its
    name or agent ID. Nothing else. The sender shows as an identity name when the stored sender is
    the agent ID of an identity in the reader's session; otherwise as stored."""
    sid, aid, name = identity["session_id"], identity["agent_id"], identity["name"]
    return _unread_rows(
        conn,
        "SELECT m.id, m.sender, m.topic, m.created_at, m.session_id, m.body, d.name AS sender_name "
        "FROM inbox m LEFT JOIN identities d ON d.agent_id = m.sender AND d.session_id = ? "
        "WHERE m.status='pending' AND ((m.session_id = ? AND m.recipient = ?) "
        "OR (m.session_id = ? AND m.recipient IN (?, ?))) ORDER BY m.created_at, m.id",
        (sid, sid, aid, LEGACY, name, aid), with_body)


def unread_legacy(conn, raw_name, *, with_body=False) -> list:
    """Pending `legacy` mail to the raw name only. For a reader with no identity."""
    return _unread_rows(
        conn,
        "SELECT m.id, m.sender, m.topic, m.created_at, m.session_id, m.body, NULL AS sender_name "
        "FROM inbox m WHERE m.status='pending' AND m.session_id = ? AND m.recipient = ? "
        "ORDER BY m.created_at, m.id",
        (LEGACY, raw_name), with_body)


def _addressed_to(name):
    """SQL for rows whose recipient is `name` or the agent ID of an identity named `name`."""
    return ("(m.recipient = ? OR m.recipient IN (SELECT agent_id FROM identities WHERE name = ?))",
            (name, name))


def unread_by_name(conn, name, *, with_body=False) -> list:
    """Pending mail in ANY session addressed to `name` or to the agent ID of an identity named
    `name`. Needs no identity. Same rows as `unread`, plus `recipient` and `status`. Reads only."""
    cond, params = _addressed_to(name)
    rows = _unread_rows(
        conn,
        "SELECT m.id, m.sender, m.topic, m.created_at, m.session_id, m.body, m.recipient, m.status, "
        "(SELECT d.name FROM identities d WHERE d.agent_id = m.sender AND d.session_id = m.session_id) "
        "AS sender_name FROM inbox m WHERE m.status='pending' AND " + cond +
        " ORDER BY m.created_at, m.id", params, with_body)
    meta = {r["id"]: r for r in conn.execute(
        "SELECT id, recipient, status FROM inbox WHERE id IN (%s)" % ",".join("?" * len(rows)),
        [r["id"] for r in rows])} if rows else {}
    for r in rows:
        r["recipient"], r["status"] = meta[r["id"]]["recipient"], meta[r["id"]]["status"]
    return rows


def ack_by_name(conn, name, ids, *, from_inflight=True) -> dict:
    """Move to `done` the pending (and, with `from_inflight`, inflight) rows addressed to `name`.
    Other IDs are `skipped`; rows already done are `already`. One transaction."""
    cond, params = _addressed_to(name)
    ids = list(ids)
    out = {"ok": [], "already": [], "skipped": []}
    _begin(conn)
    try:
        owned = {r["id"]: r["status"] for r in conn.execute(
            "SELECT m.id, m.status FROM inbox m WHERE " + cond, params)}
        movable = ("pending", "inflight") if from_inflight else ("pending",)
        for i in ids:
            st = owned.get(i)
            if st in movable:
                out["ok"].append(i)
            elif st == "done":
                out["already"].append(i)
            else:
                out["skipped"].append(i)
        t = rookery.now()
        conn.executemany(
            "UPDATE inbox SET status='done', delivered=1, delivered_at=? WHERE id=?",
            [(t, i) for i in out["ok"]])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return out


def _ack_visible(conn, cond, params, ids, from_inflight) -> dict:
    """Ack the rows of `ids` that `cond` selects (the `unread` row set, any status). One
    transaction. pending (and inflight, with `from_inflight`) -> done; done and inflight-without-flag
    -> already; every other id -> skipped."""
    ids = list(dict.fromkeys(ids))
    out = {"ok": [], "already": [], "skipped": []}
    _begin(conn)
    try:
        status = {}
        for k in range(0, len(ids), 500):          # stay under SQLite's variable limit
            part = ids[k:k + 500]
            q = ",".join("?" * len(part))
            for r in conn.execute(
                    "SELECT m.id, m.status FROM inbox m WHERE (" + cond + ") AND m.id IN (" + q + ")",
                    [*params, *part]):
                status[r["id"]] = r["status"]
        movable = ("pending", "inflight") if from_inflight else ("pending",)
        for i in ids:
            st = status.get(i)
            if st in movable:
                out["ok"].append(i)
            elif st in ("inflight", "done"):
                out["already"].append(i)
            else:
                out["skipped"].append(i)
        t = rookery.now()
        conn.executemany(
            "UPDATE inbox SET status='done', delivered=1, delivered_at=? WHERE id=?",
            [(t, i) for i in out["ok"]])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return out


def ack(conn, identity, ids, *, from_inflight=False) -> dict:
    """Move to `done` the rows `unread` returns for `identity` (its session's mail to its agent ID,
    and `legacy` mail to its name or agent ID). inflight counts as already read unless
    `from_inflight`. Other ids are `skipped`."""
    sid, aid, name = identity["session_id"], identity["agent_id"], identity["name"]
    return _ack_visible(
        conn, "(m.session_id = ? AND m.recipient = ?) OR (m.session_id = ? AND m.recipient IN (?, ?))",
        (sid, aid, LEGACY, name, aid), ids, from_inflight)


def ack_legacy(conn, raw_name, ids) -> dict:
    """Move to `done` the pending rows `unread_legacy(raw_name)` returns. Other ids are `skipped`."""
    return _ack_visible(conn, "m.session_id = ? AND m.recipient = ?", (LEGACY, raw_name), ids, False)


def claim_by_name(conn, name, ids=None) -> dict:
    """Move the pending rows of `unread_by_name(name)` (only `ids`, when given) to `inflight`."""
    cond, params = _addressed_to(name)
    _begin(conn)
    try:
        pend = [r["id"] for r in conn.execute(
            "SELECT m.id FROM inbox m WHERE m.status='pending' AND " + cond, params)]
        want = pend if ids is None else [i for i in ids if i in set(pend)]
        t = rookery.now()
        conn.executemany("UPDATE inbox SET status='inflight', claimed_at=? WHERE id=?",
                         [(t, i) for i in want])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": want, "skipped": [] if ids is None else [i for i in ids if i not in set(want)]}


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
