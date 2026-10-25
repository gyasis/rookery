#!/usr/bin/env python3
"""The T1 screener (004 T010; research D8, D10, D11). build_envelope() makes the state text of one mail.
screen_one() asks the decision model about it and writes the records.

screen_one() only records. The loop (run) acts in live mode (D13, D14, FR-012a, FR-027): see _act_pre. Records are
written through screener_t0.record and committed at once. The backend is passed in, so a test uses
screener_backend.fake(). Never imports promptchain.
"""
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import mail_gate
import rookery
import screener_backend
import screener_config
import screener_t0
import turn_block

LABELS = ["deliver", "wrong-recipient", "task-already-done", "unneeded"]
INSTRUCTIONS = (
    "You check one mail between two agents of one team. Read the mail. Pick the one label that fits.\n"
    "deliver: the recipient is the right agent, the work is not done yet, and the task needs the mail. Use it when unsure.\n"
    "wrong-recipient: the work is in the scope of another agent. The recipient does not do this work.\n"
    "task-already-done: the work was already done. The sender says so, or the acceptance list is met.\n"
    "unneeded: the sender can do this alone, or the mail asks for nothing the task needs.")
SUGGEST_INSTRUCTIONS = (
    "The mail went to the wrong agent. Pick the agent who does this work. Match the scope of each agent.\n"
    "Pick none when no agent in the list fits.")


def _session(conn, sid):
    return conn.execute("SELECT name FROM rookery_sessions WHERE session_id=?", (sid,)).fetchone()


def _name(conn, who):
    """The identity name for an agent ID, else the stored text (a legacy mail holds a raw name)."""
    row = conn.execute("SELECT name FROM identities WHERE agent_id=?", (who,)).fetchone()
    return row["name"] if row else who


def _scope(conn, sid, who):
    """The scope card of one agent of this session, or None when it has no text."""
    row = conn.execute("SELECT does, does_not FROM agent_scope WHERE agent_id=? AND session_id=?",
                       (who, sid)).fetchone()
    if row is None:
        return None
    card = {"does": json.loads(row["does"]), "does_not": json.loads(row["does_not"])}
    return card if card["does"] or card["does_not"] else None


_SELF_CHECK = {"task_complete": bool, "gap": str, "gap_in_my_scope": bool}


def _block(body):
    """The block fields of a stored block mail, or None for a plain mail. The body is the compact block
    text that turn_block stores; it parses with the same json rules (no other parser)."""
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not (isinstance(obj, dict) and obj.get("kind") in turn_block.AGENT_KINDS and isinstance(obj.get("say"), str)):
        return None
    out = {"kind": obj["kind"], "say": obj["say"]}   # a whitelist: no evidence key, no unknown key, no wrong type
    for key in ("task", "thread"):
        if isinstance(obj.get(key), str):
            out[key] = obj[key]
    sc = obj.get("self_check")
    if isinstance(sc, dict):
        keep = {k: sc[k] for k, t in _SELF_CHECK.items() if isinstance(sc.get(k), t)}
        if keep:
            out["self_check"] = keep
    read = obj.get("read")
    if isinstance(read, list) and all(isinstance(r, str) for r in read):
        out["read"] = read
    return out


def build_envelope(conn, mail_row, cfg) -> str:
    """The compact JSON envelope of one mail (D8). Holds this session's cards and criteria only (FR-013).
    No `evidence`. Empty fields are left out. The size check is screen_one's job."""
    sid = mail_row["session_id"]
    sess = _session(conn, sid)
    block = _block(mail_row["body"])
    env = {"mail": mail_row["id"], "session": sess["name"] if sess else sid}
    env["kind"] = block["kind"] if block else "plain"
    env["from"] = _name(conn, mail_row["sender"])
    env["to"] = _name(conn, mail_row["recipient"])
    env["task"] = block.get("task") if block else mail_row["topic"]
    env["thread"] = mail_row["thread"]
    if block:
        env["self_check"] = block.get("self_check")
        env["read"] = block.get("read")
    env["ask"] = block["say"] if block else mail_row["body"]
    env["from_scope"] = _scope(conn, sid, mail_row["sender"])
    env["to_scope"] = _scope(conn, sid, mail_row["recipient"])
    ctx = conn.execute("SELECT acceptance FROM session_context WHERE session_id=?", (sid,)).fetchone()
    env["acceptance"] = json.loads(ctx["acceptance"]) if ctx else None
    return json.dumps({k: v for k, v in env.items() if v not in (None, "", [], {})},
                      separators=(",", ":"), ensure_ascii=False)


def _band(conf, bands):
    if not bands:
        return "unset"
    return "accept" if conf >= bands["accept"] else "review" if conf >= bands["review"] else "escalate"


def _write(conn, mail, **kw):
    """One record, with the next attempt number for this check, then commit (record logs at insert time)."""
    for tries in (1, 2, 3):   # another writer may take the number between the read and the insert
        n = conn.execute("SELECT COALESCE(MAX(attempt), 0) FROM screen_decisions WHERE mail_id=? AND check_name=?",
                         (mail["id"], kw["check_name"])).fetchone()[0]
        try:
            rid = screener_t0.record(conn, mail_id=mail["id"], session_id=mail["session_id"], attempt=n + 1, **kw)
        except sqlite3.IntegrityError:   # the insert failed first, so no gate-log line was written
            if tries == 3:
                raise
            continue
        conn.commit()
        return rid


def _decide(conn, mail, cfg, backend, state, labels, instructions, question, stage):
    """Ask once and write the record. Returns the Answer, or None after a failure record (FR-012)."""
    mode = cfg["checks"]["t1"]
    by = "screener:" + cfg["model"]
    try:
        ans = backend.decide(state, labels, instructions, question=question)
    except screener_backend.BackendError as e:
        _write(conn, mail, stage=stage, check_name=stage, mode=mode, decided_by=by, failure=e.cause)
        return None
    band = _band(ans.confidence, (cfg.get("bands") or {}).get("t1")) if stage == "t1" else None
    _write(conn, mail, stage=stage, check_name=stage, mode=mode, decided_by=by, label=ans.label,
           confidence=ans.confidence, band=band, impl=ans.impl, usage=ans.usage)
    return ans


def _named_peer(block, names):
    """The one roster name a decline says (FR-031), or None. A name counts as a whole word, any case."""
    if not block or block["kind"] != "decline":
        return None
    found = [n for n in names if re.search(r"(?<![\w.-])%s(?![\w-]|\.\w)" % re.escape(n), block["say"], re.I)
             if n.lower() != "me"]
    return found[0] if len(found) == 1 else None


def _hunch(conn, mail, cfg, hunch, state, t1):
    """The second opinion (D11): ask hunch the same route question, only for a T1 answer in the band `review`, and
    write the record (a label, or a failure). It has no band of its own: _act_pre compares its label with T1's."""
    mode = cfg["checks"].get("hunch", "off")
    if hunch is None or mode == "off" or _band(t1.confidence, (cfg.get("bands") or {}).get("t1")) != "review":
        return
    by = "hunch:vote6:" + cfg["hunch"]["model"]
    try:
        ans = hunch.decide(state, LABELS, INSTRUCTIONS, question="route")
    except screener_backend.BackendError as e:
        _write(conn, mail, stage="hunch", check_name="hunch", mode=mode, decided_by=by, failure=e.cause)
        return
    _write(conn, mail, stage="hunch", check_name="hunch", mode=mode, decided_by=by, label=ans.label,
           confidence=ans.confidence, impl=ans.impl, usage=ans.usage)


def screen_one(conn, mail_id, cfg, backend, hunch=None):
    """Screen one mail: one T1 record (a label or a failure), a suggest record for wrong-recipient, and a hunch
    record for an answer in the band `review` (when a hunch backend is passed and checks.hunch is not off).
    Returns the T1 label, or None for a failure or a mode of off (no record then). Changes no inbox row."""
    if cfg["checks"]["t1"] == "off":
        return None
    mail = conn.execute("SELECT * FROM inbox WHERE id=?", (mail_id,)).fetchone()
    if mail is None:
        raise ValueError(f"mail {mail_id} is not in the inbox")
    mode = cfg["checks"]["t1"]
    if not screener_t0.check_decided_by("screener:" + str(cfg["model"])):
        raise ValueError(f"screener.model: {cfg['model']!r} cannot be a decided_by name")
    by = "screener:" + cfg["model"]
    try:
        state = build_envelope(conn, mail, cfg)
    except Exception as e:   # an envelope that cannot be built is a failure record, not a crash
        _write(conn, mail, stage="t1", check_name="t1", mode=mode, decided_by=by, failure="bad-envelope",
               detail=type(e).__name__)
        return None
    if len(state) > cfg["envelope_max_chars"]:   # R16: never a label, never a model call
        _write(conn, mail, stage="t1", check_name="t1", mode=mode, decided_by=by,
               failure="envelope-too-large", detail=f"chars={len(state)} limit={cfg['envelope_max_chars']}")
        return None
    block = _block(mail["body"])
    sender, first_to = _name(conn, mail["sender"]), _name(conn, mail["recipient"])
    roster = [r["name"] for r in conn.execute("SELECT name FROM identities WHERE session_id=? ORDER BY name",
                                              (mail["session_id"],))]
    peers = list(dict.fromkeys(n for n in roster if n not in (sender, first_to, "none")))
    peer = _named_peer(block, peers) if cfg["checks"].get("gates") != "off" else None
    if peer:   # the decline names the peer: keep it, no model call
        _write(conn, mail, stage="suggest", check_name="suggest", mode=cfg["checks"]["gates"],
               decided_by="t0:gates", label=peer)
    ans = _decide(conn, mail, cfg, backend, state, LABELS, INSTRUCTIONS, "route", "t1")
    if ans is not None and ans.label == "wrong-recipient" and not peer:
        sstate = state + "\nroster: " + ",".join(peers + ["none"])
        if len(sstate) > cfg["envelope_max_chars"]:   # the size limit holds for the exact text sent
            _write(conn, mail, stage="suggest", check_name="suggest", mode=mode, decided_by=by,
                   failure="envelope-too-large", detail=f"chars={len(sstate)} limit={cfg['envelope_max_chars']}")
        else:
            _decide(conn, mail, cfg, backend, sstate, peers + ["none"], SUGGEST_INSTRUCTIONS, "suggest", "suggest")
    if ans is not None:
        _hunch(conn, mail, cfg, hunch, state, ans)
    return ans.label if ans is not None else None


# --- the loop (D12, FR-005, FR-013a) ---

def backend_path():
    """Which backend decides: fake, promptchain or stdlib. The PromptChain import takes 6 s (F20)."""
    if os.environ.get("ROOKERY_SCREENER_FAKE"):
        return "fake"
    return "promptchain" if screener_backend._promptchain() is not None else "stdlib"


def _begin(conn):
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def queue(conn, sid, cutoff, limit=None, oldest=None):
    """Mail ids of one session to screen, oldest first (FR-005): no screen_mail row, a claim older than
    cutoff, or an escalated mail whose retry_at has come while the mail is still escalated (FR-012a).
    A system sender is left out. A mail that a T0 check quarantined at store has a decided row.
    A mail created before `oldest` (an epoch second) is left out: the first run skips old history."""
    rows = conn.execute(
        "SELECT i.id, i.sender, i.created_at FROM inbox i LEFT JOIN screen_mail m ON m.mail_id=i.id "
        "WHERE i.session_id=? AND i.created_at>=? "
        "AND (m.mail_id IS NULL OR m.state='queued' OR (m.state='claimed' AND m.claimed_at<?) "
        "OR (m.state='escalated' AND m.retry_at<=? AND i.status='escalated')) "
        "ORDER BY i.created_at, i.id", (sid, oldest or 0, cutoff, rookery.now())).fetchall()
    out = [r for r in rows if not (mail_gate.is_system_name(r["sender"])
                                   or mail_gate.is_system_name(_name(conn, r["sender"])))]
    return out[:limit] if limit else out


MAX_ATTEMPTS = 3   # a claim that expired after this many tries ends as failed (a mail that kills the process)
PER_SESSION = 5    # mails for one session in one pass, so one busy session does not starve the others


def _skip_approved(conn, mail_id, cfg, mode):
    """True, after a `skipped` T1 record (no call), when the mail is later mail on an approved thread (D16, FR-021).
    A decline, a recruit, a mail with a `duplicate` record, a loop hold and mail from or to an agent outside the
    pair of the first mail still get a full T1."""
    m = rookery.get_message(conn, mail_id)
    if not m["thread"] or m["reason"] == "loop" or (_block(m["body"]) or {}).get("kind") in ("decline", "recruit"):
        return False
    t = conn.execute("SELECT opened_by, first_mail FROM threads WHERE session_id=? AND thread=? "
                     "AND status='approved' AND first_mail<>?", (m["session_id"], m["thread"], mail_id)).fetchone()
    if t is None:
        return False
    first = rookery.get_message(conn, t[1])
    if first is None or {m["sender"], m["recipient"]} != {t[0], first["recipient"]}:
        return False
    if conn.execute("SELECT 1 FROM screen_decisions WHERE mail_id=? AND check_name='duplicate' AND label='duplicate'",
                    (mail_id,)).fetchone():
        return False
    _write(conn, m, stage="t1", check_name="t1", mode=mode, decided_by="t0:thread", label="skipped")
    return True


def _claim(conn, mail_id, sid, owner, cutoff, cfg=None):
    """Take the mail, an expired claim on it, or an escalated mail that is due for a retry. True when this owner
    now holds it. An expired claim with MAX_ATTEMPTS tries is not taken again: a mail that the screener holds is
    escalated with the reason screening-failed (and the notice goes out after the commit), else it is set to failed."""
    now = rookery.now()
    by = "screener:" + (cfg["model"] if cfg else "lease")
    notify = False
    _begin(conn)
    try:
        cur = conn.execute("INSERT OR IGNORE INTO screen_mail(mail_id, session_id, state, owner, claimed_at, attempts) "
                           "VALUES(?,?,'claimed',?,?,1)", (mail_id, sid, owner, now))
        if cur.rowcount == 0:   # a `queued` row (the gate stored an ask hash, T021): this claim takes it
            cur = conn.execute("UPDATE screen_mail SET state='claimed', owner=?, claimed_at=?, attempts=1 "
                               "WHERE mail_id=? AND state='queued'", (owner, now, mail_id))
        if cur.rowcount == 0:
            dead = conn.execute("SELECT 1 FROM screen_mail WHERE mail_id=? AND state='claimed' AND claimed_at<? "
                                "AND attempts>=?", (mail_id, cutoff, MAX_ATTEMPTS)).fetchone()
            if dead:
                m = rookery.get_message(conn, mail_id)
                notify = m["status"] == "held" and m["reason"] == "screening"
                if notify:
                    rookery._decide(conn, mail_id, "held", "escalated", "escalate", "screening-failed", by, commit=False)
                conn.execute("UPDATE screen_mail SET state=?, decided_at=? WHERE mail_id=?",
                             ("escalated" if notify else "failed", now, mail_id))   # no retry_at: it failed MAX_ATTEMPTS times
                _log(sid, mail_id, f"claim expired after {MAX_ATTEMPTS} tries: " + ("escalated" if notify else "set to failed"))
                cur = None
            else:
                cur = conn.execute("UPDATE screen_mail SET owner=?, claimed_at=?, attempts=attempts+1 "
                                   "WHERE mail_id=? AND state='claimed' AND claimed_at<?", (owner, now, mail_id, cutoff))
                if cur.rowcount == 0:   # a due retry of an escalated mail: back to claimed, one more try
                    cur = conn.execute("UPDATE screen_mail SET state='claimed', owner=?, claimed_at=?, attempts=attempts+1 "
                                       "WHERE mail_id=? AND state='escalated' AND retry_at<=?", (owner, now, mail_id, now))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if notify:
        _notify(conn, mail_id, "screening-failed")
    return cur is not None and cur.rowcount == 1


def _finish(conn, mail_id, owner, cfg, label, envelope, state=None, retry_at=None, commit=True):
    """End a claim: decided with the T1 label, or failed (a shadow failure is final), or `escalated` with a
    retry_at (a live failure on held mail). Only the owner may."""
    state = state or ("decided" if label else "failed")
    cur = conn.execute("UPDATE screen_mail SET state=?, envelope=?, final_label=?, final_by=?, decided_at=?, retry_at=? "
                 "WHERE mail_id=? AND owner=? AND state='claimed'",
                 (state, envelope, label, "screener:" + cfg["model"] if label else None, rookery.now(), retry_at,
                  mail_id, owner))
    if cur.rowcount == 0:   # the lease was lost: another owner holds the claim, or it ended
        _log(None, mail_id, f"claim not finished: {owner} no longer holds it")
    if commit:
        conn.commit()


def _last(conn, mail_id, stage, attempt=None):
    """The newest record of one stage, or of one attempt of it."""
    return conn.execute("SELECT id, label, failure, band, attempt, mode FROM screen_decisions WHERE mail_id=? AND stage=? "
                        "AND (? IS NULL OR attempt=?) ORDER BY id DESC LIMIT 1", (mail_id, stage, attempt, attempt)).fetchone()


def _kind(conn, mail_id):
    """How live mode treats a mail (D13, D14): `pre` is held with the reason screening (or escalated by a failed
    screening, due for a retry): the screener acts. Other held or escalated mail is the user's (ROOKERY_HOLD): shadow
    records only. Anything else was delivered at once (`post`): a record and a notice, no action."""
    m = rookery.get_message(conn, mail_id)
    if m["status"] == "held" and m["reason"] == "screening":
        return "pre"
    if m["status"] == "escalated" and conn.execute(
            "SELECT 1 FROM screen_mail WHERE mail_id=? AND retry_at IS NOT NULL", (mail_id,)).fetchone():
        return "pre"
    return "shadow" if m["status"] in ("held", "escalated") else "post"


def retry_delay(cfg, attempts):
    """Seconds until the next try of a failed screening: 60 per try so far, at most timeout_s."""
    return min(cfg["timeout_s"], 60 * max(1, attempts))


def _escalate_pre(conn, mail_id, owner, cfg, envelope, reason, rec_id=None, retry=True, label=None):
    """Escalate a mail that the screener holds, then send the notice (FR-012a, FR-029). The status change, the
    `action` of the record and the end of the claim commit together. A mail that is already escalated (a failed retry)
    stays so, and sends no second notice. A `retry` has a retry_at, so a later pass asks again. Raises when it
    cannot: the caller leaves the claim to expire. Returns False when the mail is no longer held or escalated (a
    person decided it first): nothing changes then."""
    by = "screener:" + cfg["model"]
    _begin(conn)
    notify = False
    try:
        status = rookery.get_message(conn, mail_id)["status"]
        if status not in ("held", "escalated"):
            conn.rollback()
            return False
        attempts = conn.execute("SELECT attempts FROM screen_mail WHERE mail_id=?", (mail_id,)).fetchone()[0]
        if status == "held":
            rookery._decide(conn, mail_id, "held", "escalated", "escalate", reason, by, commit=False)
            notify = True
        if rec_id:
            conn.execute("UPDATE screen_decisions SET action='escalate' WHERE id=?", (rec_id,))
        _finish(conn, mail_id, owner, cfg, label, envelope, state="escalated",
                retry_at=rookery.now() + retry_delay(cfg, attempts) if retry else None, commit=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if notify:
        _notify(conn, mail_id, reason)
    return True


def _already_decided(conn, mail_id, owner, cfg, envelope):
    """A person decided the mail before the screener acted: change nothing (the record keeps action `none`) and
    end the claim as decided. A delivered mail gets the post notice. Returns True."""
    rec = _last(conn, mail_id, "t1")
    _begin(conn)
    try:
        _finish(conn, mail_id, owner, cfg, rec["label"] if rec else None, envelope, state="decided", commit=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _post_notice(conn, mail_id)
    return True


def _act_pre(conn, mail_id, owner, cfg, envelope):
    """Act on one screened mail that the screener holds (FR-027, FR-012a, D11). The status change, the `action` of
    the T1 record and the end of the claim commit together. In the band `accept`, deliver releases and a reject label
    quarantines with that label (never a loop reason, FR-002) and `suggest`. In the band `review`, when hunch is live,
    a hunch label that equals the T1 label acts the same way; a disagreement or a hunch failure escalates. Any other
    case escalates, with no retry (the same answer would come back). A failure escalates, with a retry_at. Nothing else releases a held mail: no
    lease, no time. A mail that a person decided first is left as it is: action `none`, the claim ends as decided.
    Returns True when the claim is finished here, False when the mail changed under us (the caller ends the claim)."""
    by = "screener:" + cfg["model"]
    rec = _last(conn, mail_id, "t1")
    if rec is None:
        return False
    skipped = rec["label"] == "skipped"   # an approved thread (D16): no band, released as it is
    if skipped:
        by = "t0:thread"
    if rec["label"] and rec["band"] != "accept" and not skipped:
        reason = f"low-confidence {rec['label']}"
        h = _last(conn, mail_id, "hunch") if rec["band"] == "review" and cfg["checks"].get("hunch") == "live" else None
        if h is not None and (h["id"] < rec["id"] or h["mode"] != "live"):   # an earlier try, or a shadow record: no act
            h = None
        if h is not None and h["label"] == rec["label"]:
            pass   # agreement (D11): the label acts as in the accept band, below. An act by agreement counts as a
                   # reviewed deliver, so a deliver of the first mail approves its thread (D16).
        else:
            if h is not None:
                reason = f"hunch {h['failure']}" if h["failure"] else "t1 and hunch disagree"
            return _escalate_pre(conn, mail_id, owner, cfg, envelope, reason, rec["id"], retry=False,
                                 label=rec["label"]) or _already_decided(conn, mail_id, owner, cfg, envelope)
    if not rec["label"]:
        return _escalate_pre(conn, mail_id, owner, cfg, envelope, rec["failure"], rec["id"]) \
            or _already_decided(conn, mail_id, owner, cfg, envelope)
    _begin(conn)
    try:
        if rookery.get_message(conn, mail_id)["status"] not in ("held", "escalated"):
            conn.rollback()
            return _already_decided(conn, mail_id, owner, cfg, envelope)
        if rec["label"] in ("deliver", "skipped"):
            rookery._decide_from_open(conn, mail_id, "pending", "release", None, by, commit=False)
            action = "release"
            if rec["label"] == "deliver":   # the first mail of a thread was delivered: the thread is approved (D16)
                m = rookery.get_message(conn, mail_id)
                if m["thread"]:
                    conn.execute("UPDATE threads SET status='approved' WHERE session_id=? AND thread=? AND first_mail=? "
                                 "AND status IN ('held','open')", (m["session_id"], m["thread"], mail_id))
        else:
            sug = _last(conn, mail_id, "suggest", rec["attempt"])
            peer = sug["label"] if sug and sug["label"] not in (None, "none") else None
            rookery.quarantine(conn, mail_id, rec["label"], by, suggest=peer, commit=False,
                               bounce_limit=mail_gate.loop_limit())
            action = "quarantine"
            m = rookery.get_message(conn, mail_id)   # a veto closes the thread, same transaction (D16, FR-022a)
            if m["thread"]:
                conn.execute("UPDATE threads SET status='closed', closed_by=?, closed_at=?, close_reason='veto' "
                             "WHERE session_id=? AND thread=? AND status!='closed'",
                             (by, rookery.now(), m["session_id"], m["thread"]))
        conn.execute("UPDATE screen_decisions SET action=? WHERE id=?", (action, rec["id"]))
        _finish(conn, mail_id, owner, cfg, rec["label"], envelope, commit=False)
        conn.commit()
    except ValueError as e:   # the mail is no longer held: a person decided it first
        conn.rollback()
        _log(None, mail_id, f"no action: {e}")
        return False
    except BaseException:
        conn.rollback()
        raise
    return True


def _notify(conn, mail_id, reason):
    try:
        screener_t0.notify_escalated(conn, mail_id, reason)
    except Exception as e:   # the action stands; a failed notice is logged
        conn.rollback()
        _log(None, mail_id, f"notice failed: {type(e).__name__}: {e}")


def _post_notice(conn, mail_id):
    """A mail that is delivered when the screener decides (post mode, or a person released it first) cannot be taken
    back: a failure or a reject label gets the notice (D13, D14)."""
    rec = _last(conn, mail_id, "t1")
    reason = rec and (f"no answer ({rec['failure']})" if rec["failure"] else rec["label"] if rec["label"] not in ("deliver", "skipped") else None)
    mail = rookery.get_message(conn, mail_id)
    if reason and mail["status"] not in ("held", "escalated", "quarantined"):
        try:
            screener_t0.notify_delivered(conn, mail_id, reason)
        except Exception as e:   # a failed notice is logged
            conn.rollback()
            _log(None, mail_id, f"notice failed: {type(e).__name__}: {e}")


def _log(session, mail_id, cause):
    turn_block.log_event(os.path.expanduser("~/.rookery"), stream="screener", session=session, msg_id=mail_id,
                         outcome="error", reason=cause)


# --- T2 (D15, FR-003, FR-009) ---

T2_MAX_TRIES = 3          # failure records of one mail, then it waits for a person
T2_MAX_OUT = 1 << 20      # bytes of stdout that one run may write


def _t2_mine(row) -> bool:
    """True when a screener judgement holds the escalated mail (F1, F2, F7): decided_by `screener:...` and a reason of
    T1 or hunch (an allow-list). A person's escalation, a failed screen and an envelope that is too large are not."""
    reason = row["reason"] or ""
    return (row["status"] == "escalated" and (row["decided_by"] or "").startswith("screener:")
            and (reason.startswith("low-confidence ") or reason.startswith("hunch ") or reason == "t1 and hunch disagree"))


def _t2_batch(conn, cfg):
    """The escalated mails to send to T2: oldest first over all sessions (not per session), at most t2.batch, none older
    than max_age_s. Only a screener judgement (see _t2_mine) with no T1 retry pending. A mail with a t2 label, or with
    T2_MAX_TRIES t2 failures, is not sent again: it waits for a person."""
    return conn.execute(
        "SELECT i.* FROM inbox i WHERE i.status='escalated' AND i.created_at>=? AND i.decided_by LIKE 'screener:%' "
        "AND (i.reason LIKE 'low-confidence %' OR i.reason LIKE 'hunch %' OR i.reason='t1 and hunch disagree') "
        "AND NOT EXISTS (SELECT 1 FROM screen_mail s WHERE s.mail_id=i.id AND s.retry_at IS NOT NULL) "
        "AND NOT EXISTS (SELECT 1 FROM screen_decisions d WHERE d.mail_id=i.id AND d.stage='t2' AND d.label IS NOT NULL) "
        "AND (SELECT COUNT(*) FROM screen_decisions d WHERE d.mail_id=i.id AND d.stage='t2' AND d.failure IS NOT NULL) < ? "
        "ORDER BY i.created_at, i.id LIMIT ?",
        (rookery.now() - cfg["max_age_s"], T2_MAX_TRIES, cfg["t2"]["batch"])).fetchall()


def _t2_run(command, text, timeout):
    """Run the command once in its own process group. Returns (stdout, None) or (None, why). On a timeout the whole group
    is killed, so a child that holds the pipes cannot hold us."""
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, shell=False, start_new_session=True)
    except (OSError, ValueError) as e:
        return None, type(e).__name__
    try:
        out, err = proc.communicate(text, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return None, "timeout"
    except (OSError, ValueError) as e:
        return None, type(e).__name__
    if proc.returncode != 0:
        return None, f"exit={proc.returncode}" + (f": {err[:200]}" if err else "")
    if len(out) > T2_MAX_OUT:
        return None, "output too large"
    return out, None


def _t2_lines(command, mails, cfg):   # mails: (inbox row, envelope text) pairs
    """Run the command once. Returns ({mail_id: (label, reason)}, None), or ({}, why) for a failed run. A line with a bad
    shape, a label outside LABELS or a mail_id outside the batch is dropped (the last one is logged)."""
    lines = [json.dumps({**json.loads(env), "mail_id": m["id"]}, ensure_ascii=False) for m, env in mails]
    stdout, why = _t2_run(command, "\n".join(lines) + "\n", cfg["timeout_s"])
    if why:
        return {}, why
    ids = {m["id"] for m, _ in mails}
    out = {}
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("label") not in LABELS or type(obj.get("mail_id")) is not int:
            continue
        if obj["mail_id"] not in ids:
            _log(None, obj["mail_id"], "t2 answered a mail that is not in the batch")
            continue
        out.setdefault(obj["mail_id"], (obj["label"], obj.get("reason")))
    return out, None


def _t2_act(conn, mail, rec_id, label, by):
    """Live act on one T2 label, one transaction with the `action` of the record: deliver releases, a reject label
    quarantines and closes the thread with a veto (as _act_pre does). No user call: only a person writes those. A deliver
    does not approve the thread. The mail is checked again here: only a screener judgement is acted on."""
    _begin(conn)
    try:
        if not _t2_mine(rookery.get_message(conn, mail["id"])):
            conn.rollback()   # a person decided it first, or a person holds it
            return
        if label == "deliver":   # no thread approval: only T1 in the accept band, or hunch by agreement, approves (D16)
            rookery._decide_from_open(conn, mail["id"], "pending", "release", None, by, commit=False)
            action = "release"
        else:
            rookery.quarantine(conn, mail["id"], label, by, commit=False, bounce_limit=mail_gate.loop_limit())
            action = "quarantine"
            if mail["thread"]:
                conn.execute("UPDATE threads SET status='closed', closed_by=?, closed_at=?, close_reason='veto' "
                             "WHERE session_id=? AND thread=? AND status!='closed'",
                             (by, rookery.now(), mail["session_id"], mail["thread"]))
        conn.execute("UPDATE screen_decisions SET action=? WHERE id=?", (action, rec_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def t2_pass(conn, cfg) -> int:
    """One T2 pass (D15). No command, or checks.t2 off: nothing is called and escalated mail waits (FR-003). Otherwise one
    command run for at most t2.batch mails. Each valid answer is a t2 record; in live mode it also acts. A mail with no
    valid answer keeps its status and gets a failure record `bad-answer`. In shadow mode nothing changes status.
    Returns the number of mails sent. Never raises: a failure is logged and the mails stay escalated."""
    mode = cfg["checks"]["t2"]
    command = cfg["t2"]["command"]
    if not command or mode == "off":
        return 0
    try:
        by = "t2:" + screener_config.t2_name(cfg["t2"])
        if not screener_t0.check_decided_by(by):
            raise ValueError(f"screener.t2.name: {by!r} cannot be a decided_by name")
        conn.rollback()
        mails = _t2_batch(conn, cfg)
        if not mails:
            return 0
        sent = []   # (mail, the stored envelope of screen_mail, else a fresh one)
        for m in list(mails):
            why = None
            try:
                row = conn.execute("SELECT envelope FROM screen_mail WHERE mail_id=?", (m["id"],)).fetchone()
                env = row and row["envelope"]
                if not env:
                    env = build_envelope(conn, m, cfg)
                    if len(env) > cfg["envelope_max_chars"]:
                        why = "envelope-too-large"
                if not why and not isinstance(json.loads(env), dict):
                    why = "bad-envelope"
            except ValueError:
                why = "bad-envelope"
            if why:
                mails.remove(m)
                _write(conn, m, stage="t2", check_name="t2", mode=mode, decided_by=by, failure=why, detail=why)
            else:
                sent.append((m, env))
        if not sent:
            return 0
        mails = [m for m, _ in sent]
        answers, why = _t2_lines(command, sent, cfg)
    except Exception as e:
        conn.rollback()
        _log(None, None, f"t2: {type(e).__name__}: {e}")
        return 0
    for m in mails:
        try:
            if m["id"] in answers:
                label, reason = answers[m["id"]]
                rec = _write(conn, m, stage="t2", check_name="t2", mode=mode, decided_by=by, label=label,
                             detail=reason[:500] if isinstance(reason, str) else None)
                if mode == "live":
                    _t2_act(conn, m, rec, label, by)
            else:
                _write(conn, m, stage="t2", check_name="t2", mode=mode, decided_by=by, failure="bad-answer",
                       detail=why or "no valid line")
        except Exception as e:   # the mail stays escalated; the next mail goes on
            conn.rollback()
            _log(None, m["id"], f"t2: {type(e).__name__}: {e}")
    return len(mails)


def open_sessions(conn, names=None):
    """(session_id, name) rows: the open sessions, or the ones named (a name or an ID). Raises ValueError for an unknown one."""
    if not names:
        return [(r["session_id"], r["name"]) for r in conn.execute(
            "SELECT session_id, name FROM rookery_sessions WHERE state='open' ORDER BY created_at, name")]
    out = []
    for n in names:
        r = conn.execute("SELECT session_id, name FROM rookery_sessions WHERE name=? OR session_id=?", (n, n)).fetchone()
        if r is None:
            raise ValueError(f"session {n!r} does not exist")
        out.append((r["session_id"], r["name"]))
    return out


def _screen_session(conn, sid, name, cfg, backend, owner, cutoff, stop, hunch=None):
    """Screen up to PER_SESSION mails of one session. A mail that raises is logged and ends as failed; the next
    mail goes on. Returns the number of mails taken. The lease (lease_s, at least twice timeout_s by
    screener_config.check) is longer than one decision, so the owner is not rechecked before a write."""
    taken = 0
    oldest = rookery.now() - cfg["max_age_s"]
    for row in queue(conn, sid, cutoff, limit=PER_SESSION, oldest=oldest):
        if stop:
            break
        mail_id = row["id"]
        if not _claim(conn, mail_id, sid, owner, cutoff, cfg):
            continue
        taken += 1
        env = label = None
        screened = False
        live = cfg["checks"]["t1"] == "live"
        kind = _kind(conn, mail_id) if live else None
        m = rookery.get_message(conn, mail_id)
        stranded = not live and m["status"] == "held" and m["reason"] == "screening"   # t1 left live by a hand edit
        scfg = cfg
        if kind == "shadow":   # ROOKERY_HOLD mail: the records say shadow, and nothing acts
            scfg = {**cfg, "checks": {**cfg["checks"], "t1": "shadow"}}
        try:
            env = build_envelope(conn, conn.execute("SELECT * FROM inbox WHERE id=?", (mail_id,)).fetchone(), cfg)
        except Exception:
            pass   # screen_one writes the bad-envelope record
        try:
            if live and _skip_approved(conn, mail_id, cfg, scfg["checks"]["t1"]):
                label = "skipped"
            else:
                label = screen_one(conn, mail_id, scfg, backend, **({"hunch": hunch} if hunch else {}))
            screened = True
            if kind == "pre" and _act_pre(conn, mail_id, owner, cfg, env):
                continue
            if stranded and _escalate_pre(conn, mail_id, owner, cfg, env, "screening-stopped", retry=False):
                continue   # fail closed: nothing acts on it any more, so a person decides
        except Exception as e:   # a poison mail: log it, end the claim as failed, go on
            conn.rollback()
            label = None
            _log(name, mail_id, f"{type(e).__name__}: {e}")
            if kind == "pre":   # fail closed: the held mail goes to a person, never to `failed` or silence
                try:
                    _escalate_pre(conn, mail_id, owner, cfg, env, "screening-failed") or \
                        _already_decided(conn, mail_id, owner, cfg, env)
                except Exception as e2:   # leave the claim to expire: _claim escalates it after MAX_ATTEMPTS
                    conn.rollback()
                    _log(name, mail_id, f"escalation failed: {type(e2).__name__}: {e2}")
                continue
        _finish(conn, mail_id, owner, cfg, label, env)
        if kind == "post" and screened:
            _post_notice(conn, mail_id)
    return taken


def run(sessions=None, once=False, poll=2.0):
    """Screen the queue of each session, PER_SESSION mails at a time, until a pass screens nothing. An error is
    logged and the loop goes on. SIGTERM ends it after the current mail. The configuration is read on each
    pass; t1 off claims nothing. Returns 0, or 2 when `once` met a configuration or session error."""
    stop = []
    old = None
    try:
        old = signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    except ValueError:   # not the main thread: no handler
        pass
    owner = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
    conn = rookery.connect(turn_block.db_path())
    code = 0
    try:
        while not stop:
            taken = 0
            try:
                cfg = screener_config.load()
                screener_config.check(cfg)
            except (ValueError, OSError) as e:
                _log(None, None, str(e))
                print(f"rookery: {e}", file=sys.stderr)
                cfg = None
                code = 2
            if cfg is not None and cfg["checks"]["t1"] != "off":
                try:
                    backend = screener_backend.system_one(cfg)
                except Exception as e:
                    _log(None, None, f"{type(e).__name__}: {e}")
                    backend = None
                    code = 2
                hunch = None
                if cfg["checks"].get("hunch", "off") != "off":
                    try:   # no second opinion: a review-band answer escalates (fail closed)
                        hunch = screener_backend.hunch_vote(cfg)
                    except Exception as e:
                        _log(None, None, f"hunch backend: {type(e).__name__}: {e}")
                cutoff = rookery.now() - cfg["lease_s"]
                for n in (sessions or [None]) if backend is not None else ():
                    try:
                        found = open_sessions(conn, [n] if n else None)
                    except Exception as e:   # a session that vanished
                        conn.rollback()
                        _log(n, None, f"{type(e).__name__}: {e}")
                        code = 2
                        continue
                    for sid, name in found:
                        try:
                            taken += _screen_session(conn, sid, name, cfg, backend, owner, cutoff, stop, hunch)
                        except Exception as e:
                            conn.rollback()
                            _log(name, None, f"{type(e).__name__}: {e}")
                            code = 2
            if cfg is not None:
                taken += t2_pass(conn, cfg)   # no t1 needed: it reads escalated mail only
            if taken:
                continue   # work was done: look again at once, until every queue is empty
            if once or stop:
                break
            for _ in range(max(1, int(poll * 10))):   # sleep in short steps so SIGTERM ends it soon
                if stop:
                    break
                time.sleep(0.1)
    finally:
        conn.close()
        if old is not None:
            signal.signal(signal.SIGTERM, old)
    return code if once else 0
