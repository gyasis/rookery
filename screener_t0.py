"""screener_t0 -- the screening record writer and the decided_by check (004 T007, FR-010, FR-012, FR-014, FR-033).

record() inserts one screen_decisions row and writes one gate-log line with path "screener".
check_decided_by() matches the five forms of FR-010. Standard library plus turn_block only: the hook path
imports this module, so it never imports screener, screener_backend or promptchain.
"""
import math
import re

import rookery
import turn_block

STAGES = ("t0", "t1", "suggest", "hunch", "t2")
CHECK_NAMES = ("injection", "duplicate", "budget", "thread", "gates", "t1", "suggest", "hunch", "t2")
BANDS = ("accept", "review", "escalate", "unset")
ACTIONS = ("none", "release", "quarantine", "hold", "escalate", "refuse")
MODES = ("shadow", "live")

# t0:<check>, screener:<model>, hunch:vote6:<model>, t2:<name>, human:<name>. A model name can hold ":" (tev1:4b).
_DECIDED_BY = re.compile(r"(?:t0:[a-z0-9_-]+|screener:{n}|hunch:vote6:{n}|t2:[A-Za-z0-9][A-Za-z0-9._ -]*|human:{n})".format(
    n=r"[A-Za-z0-9][A-Za-z0-9._:@/-]*"))


def check_decided_by(text) -> bool:
    """True when text has one of the five forms of FR-010."""
    return isinstance(text, str) and _DECIDED_BY.fullmatch(text) is not None


def _token(usage, key):
    value = (usage or {}).get(key)
    if value is not None and (type(value) is not int):
        raise ValueError(f"usage.{key} must be an int or None, not {value!r}")
    return value


def record(conn, *, mail_id, session_id, stage, check_name, mode, decided_by, label=None, failure=None,
           attempt=1, confidence=None, band=None, action="none", detail=None, impl=None, usage=None) -> int:
    """Insert one screen_decisions row, write one gate-log line, return the row id.

    The caller owns the transaction and commits. detail holds a pattern name, a hash or numbers, never a mail body.
    Raises ValueError for a bad value; nothing is written then.
    The gate-log line is written at insert time, not at commit, so commit right after record().
    """
    if (label is None) == (failure is None):
        raise ValueError("a screening record needs exactly one of label and failure")
    for name, value, allowed in (("stage", stage, STAGES), ("check_name", check_name, CHECK_NAMES),
                                 ("mode", mode, MODES), ("action", action, ACTIONS)):
        if value not in allowed:
            raise ValueError(f"{name} {value!r} is not one of {', '.join(allowed)}")
    if band is not None and band not in BANDS:
        raise ValueError(f"band {band!r} is not one of {', '.join(BANDS)}")
    if mode == "shadow" and action != "none":
        raise ValueError(f"action must be none in shadow mode, not {action!r}")
    if not check_decided_by(decided_by):
        raise ValueError(f"decided_by {decided_by!r} has no form of FR-010")
    if type(attempt) is not int or attempt < 1:
        raise ValueError(f"attempt must be an int of 1 or more, not {attempt!r}")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                   or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError(f"confidence must be a number from 0 to 1 or None, not {confidence!r}")
    if label is not None and (not isinstance(label, str) or not label):
        raise ValueError("label must be non-empty text")
    if detail is not None and (not isinstance(detail, str) or len(detail) > 500):
        raise ValueError("detail must be text of at most 500 characters")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("usage must be a dict or None")
    prompt_tokens, reasoning_tokens = _token(usage, "prompt_tokens"), _token(usage, "reasoning_tokens")
    cur = conn.execute(
        "INSERT INTO screen_decisions(mail_id, session_id, stage, check_name, attempt, label, confidence, band,"
        " mode, action, decided_by, backend_impl, failure, detail, prompt_tokens, reasoning_tokens, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mail_id, session_id, stage, check_name, attempt, label, confidence, band, mode, action, decided_by,
         impl, failure, detail, prompt_tokens, reasoning_tokens, rookery.now()))
    turn_block.log_event(None, "gate", path="screener", session=session_id, msg_id=mail_id, stage=stage,
                         check=check_name, label=label, confidence=confidence, mode=mode, action=action,
                         decided_by=decided_by, failure=failure)
    return cur.lastrowid


# T0 records that stop a mail. A person who releases such a mail says the record was wrong (D20).
T0_BLOCKING = ("injection-suspect", "duplicate", "over-budget", "would-deny", "denied",
               "g1-task-complete", "g2-no-gap", "g3-gap-in-scope", "g5-ask-not-allowed")
CALL_SOURCES = ("mark", "release", "quarantine", "unquarantine")


def human_by(name):
    """The stored form of a person: human:<name>. A name that already has the prefix stays as it is."""
    for other in ("screener:", "t0:", "hunch:", "t2:"):
        if name.startswith(other):
            raise ValueError(f"--by {name!r}: a person's name cannot start with {other}")
    by = name if name.startswith("human:") else f"human:{name}"
    if not check_decided_by(by):
        raise ValueError(f"--by {name!r}: use letters, digits and . _ : @ / - only, with no space")
    return by


def add_call(conn, decision_id, mail_id, call_label, verdict, source, by):
    """Insert one screen_calls row. The caller owns the transaction."""
    if verdict not in ("right", "wrong"):
        raise ValueError(f"verdict must be right or wrong, not {verdict!r}")
    if source not in CALL_SOURCES:
        raise ValueError(f"source {source!r} is not one of {', '.join(CALL_SOURCES)}")
    if not call_label:
        raise ValueError("call_label must be non-empty text")
    conn.execute("INSERT INTO screen_calls(decision_id, mail_id, call_label, verdict, source, \"by\", created_at)"
                 " VALUES(?,?,?,?,?,?,?)", (decision_id, mail_id, call_label, verdict, source, by, rookery.now()))


def calls_for_action(conn, mail_id, action, reason, by) -> int:
    """Write the user calls of one manual action (D20, data-model mapping table). Return how many.

    One call for each t0, t1, hunch and t2 decision of the mail that has a label. A `suggest` decision gets a call
    only from `mark`. A T0 record that blocks nothing gets none. `skipped` is no answer, so it gets none.
    The caller owns the transaction: it commits the status change and these rows together.
    """
    if action not in ("release", "quarantine", "unquarantine"):
        raise ValueError(f"action {action!r} is not release, quarantine or unquarantine")
    if action == "quarantine" and not reason:
        raise ValueError("a quarantine call needs the reason")
    # rookery._decide commits before it finds a wrong status, so check here: no call may outlive a refused action.
    if not conn.in_transaction:   # the lock keeps the status as checked until the status change commits
        conn.execute("BEGIN IMMEDIATE")
    row = rookery.get_message(conn, mail_id)
    start = ("quarantined",) if action == "unquarantine" else ("held", "escalated")
    if row is None or row["status"] not in start:
        raise ValueError(f"message #{mail_id} is {row['status'] if row else 'missing'}, expected {' or '.join(start)}")
    rows = conn.execute("SELECT id, stage, label FROM screen_decisions WHERE mail_id=? AND label IS NOT NULL"
                        " AND label != 'skipped' AND stage IN ('t0','t1','hunch','t2') ORDER BY id",
                        (mail_id,)).fetchall()
    n = 0
    for r in rows:
        if r[1] == "t0":
            if r[2] not in T0_BLOCKING:
                continue
            blocked = action == "quarantine"
            add_call(conn, r[0], mail_id, r[2] if blocked else "deliver", "right" if blocked else "wrong", action, by)
        else:
            call = reason if action == "quarantine" else "deliver"
            add_call(conn, r[0], mail_id, call, "right" if call == r[2] else "wrong", action, by)
        n += 1
    return n


# --- live actions (004 T014, D13, D14) ---

ESCALATION_NOTICE = ("Mail {id} from {sender} to {recipient} waits for a decision. Reason: {reason}. "
                     "Use rookery screen release or rookery screen quarantine.")
# The mail was delivered before the screener decided (post mode, or a person released it first): a notice only.
POST_NOTICE = ("Rookery delivered mail {id} from {sender} to {recipient}. The screener says {label}. "
               "Use rookery screen review to check it.")


def pre_hold(conn, recipient, cfg):
    """The `pre_store` answer of the gate (D13): ("held", "screening") when t1 is live and the review mode of the
    recipient is `pre`, else None. `recipient` is the identities row, or None for a remote address. A mail that
    ROOKERY_HOLD holds is not touched here: it keeps no reason, so the screener only records shadow decisions on it.
    Cheap on purpose: no query, no import of the screener."""
    if recipient is None or cfg["checks"].get("t1") != "live" or rookery.hold_enabled():
        return None
    review = cfg["review_mode"]
    if review["agents"].get(recipient["name"], review["default"]) != "pre":
        return None
    return ("held", "screening")


THREAD_BOUNCE = ("Rookery did not deliver this mail. The thread {thread} has closed. "
                 "Start a new thread for new work.")


def thread_check(conn, session_id, thread, sender, held, cfg):
    """The thread step of the gate (D16, FR-020, FR-022), inside the gate transaction, before the mail is stored.
    A new thread value makes the `threads` row (`held` when `held`, else `open`; first_mail is set by
    thread_stored). Returns "new" for that, "denied" for a closed thread with B3 live (a `denied` record with
    mail_id NULL is written: the mail is not stored), "would-deny" for a closed thread with B3 in shadow mode
    (thread_stored writes the record once the mail has an ID), else None. Mail with no thread does nothing.
    Cheap on purpose: one query. A cfg of None (unreadable configuration) leaves B3 off."""
    if not thread:
        return None
    row = conn.execute("SELECT status FROM threads WHERE session_id=? AND thread=?", (session_id, thread)).fetchone()
    if row is None:
        conn.execute("INSERT INTO threads(session_id, thread, status, opened_by, first_mail, opened_at) "
                     "VALUES(?,?,?,?,0,?)", (session_id, thread, "held" if held else "open", sender, rookery.now()))
        return "new"
    mode = cfg["checks"].get("thread", "off") if cfg else "off"
    if row[0] != "closed" or mode == "off":
        return None
    if mode == "live":
        record(conn, mail_id=None, session_id=session_id, stage="t0", check_name="thread", mode="live",
               decided_by="t0:thread", label="denied", action="refuse", detail=f"thread={thread}"[:500])
        return "denied"
    return "would-deny"


def thread_stored(conn, session_id, thread, mail_id, verdict, cfg):
    """After the gate stores the mail: a "new" thread gets its first_mail (and is `held` when the stored mail is
    held, whatever the reason), a "would-deny" gets its shadow record. The caller commits."""
    if verdict == "new":
        conn.execute("UPDATE threads SET first_mail=?, status=CASE WHEN (SELECT status FROM inbox WHERE id=?)='held' "
                     "THEN 'held' ELSE status END WHERE session_id=? AND thread=?",
                     (mail_id, mail_id, session_id, thread))
    elif verdict == "would-deny":
        record(conn, mail_id=mail_id, session_id=session_id, stage="t0", check_name="thread", mode="shadow",
               decided_by="t0:thread", label="would-deny", detail=f"thread={thread}"[:500])


def _name(conn, who):
    row = conn.execute("SELECT name FROM identities WHERE agent_id=?", (who,)).fetchone()
    return row["name"] if row else who


def _notify_boss(conn, mail_id, template, title, **fields):
    import mail_gate    # local: mail_gate imports this module
    import plugins
    mail = rookery.get_message(conn, mail_id)
    if mail is None:
        raise ValueError(f"mail {mail_id} is not in the inbox")
    sender, recipient = _name(conn, mail["sender"]), _name(conn, mail["recipient"])
    text = template.format(id=mail_id, sender=sender, recipient=recipient, **fields)
    boss = conn.execute("SELECT agent_id FROM up_agents WHERE session_id=? AND role='boss' ORDER BY agent_id LIMIT 1",
                        (mail["session_id"],)).fetchone()
    notice = (mail_gate.system_send(conn, "rookery", boss[0], text, session_id=mail["session_id"])
              if boss else None)
    plugins.notify("escalated", title, text, mail_id=mail_id, sender=sender, recipient=recipient, **fields)
    return notice


def notify_escalated(conn, mail_id, reason):
    """The escalation notice (FR-029): a mail from `rookery` to the boss of the session (the up_agents row with
    role `boss`, when there is one), and the plugin sink event `escalated`. No mail body in either. Commits.
    Returns the ID of the notice mail, or None when the session has no boss."""
    return _notify_boss(conn, mail_id, ESCALATION_NOTICE, f"mail {mail_id} waits for a decision", reason=reason)


def notify_delivered(conn, mail_id, label):
    """The notice for a mail that is already delivered (POST_NOTICE). Same boss mail and sink event."""
    return _notify_boss(conn, mail_id, POST_NOTICE, f"mail {mail_id} was delivered", reason=label, label=label)
