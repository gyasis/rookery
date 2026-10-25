#!/usr/bin/env python3
"""The T1 screener (004 T010; research D8, D10, D11). build_envelope() makes the state text of one mail.
screen_one() asks the decision model about it and writes the records.

Nothing here changes the inbox: this task only records. Records are written through screener_t0.record and
committed at once. The backend is passed in, so a test uses screener_backend.fake(). Never imports promptchain.
"""
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
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


def screen_one(conn, mail_id, cfg, backend):
    """Screen one mail: one T1 record (a label or a failure), and a suggest record for wrong-recipient.
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
    """Mail ids of one session to screen, oldest first (FR-005): no screen_mail row, or a claim older than
    cutoff. A system sender is left out. A mail that a T0 check quarantined at store has a decided row.
    A mail created before `oldest` (an epoch second) is left out: the first run skips old history."""
    rows = conn.execute(
        "SELECT i.id, i.sender, i.created_at FROM inbox i LEFT JOIN screen_mail m ON m.mail_id=i.id "
        "WHERE i.session_id=? AND i.created_at>=? "
        "AND (m.mail_id IS NULL OR (m.state='claimed' AND m.claimed_at<?)) "
        "ORDER BY i.created_at, i.id", (sid, oldest or 0, cutoff)).fetchall()
    out = [r for r in rows if not (mail_gate.is_system_name(r["sender"])
                                   or mail_gate.is_system_name(_name(conn, r["sender"])))]
    return out[:limit] if limit else out


MAX_ATTEMPTS = 3   # a claim that expired after this many tries ends as failed (a mail that kills the process)
PER_SESSION = 5    # mails for one session in one pass, so one busy session does not starve the others


def _claim(conn, mail_id, sid, owner, cutoff):
    """Take the mail, or an expired claim on it. True when this owner now holds it. An expired claim with
    MAX_ATTEMPTS tries is set to failed, not taken again."""
    now = rookery.now()
    _begin(conn)
    try:
        cur = conn.execute("INSERT OR IGNORE INTO screen_mail(mail_id, session_id, state, owner, claimed_at, attempts) "
                           "VALUES(?,?,'claimed',?,?,1)", (mail_id, sid, owner, now))
        if cur.rowcount == 0:
            dead = conn.execute("UPDATE screen_mail SET state='failed', decided_at=? "
                                "WHERE mail_id=? AND state='claimed' AND claimed_at<? AND attempts>=?",
                                (now, mail_id, cutoff, MAX_ATTEMPTS)).rowcount
            if dead:
                _log(sid, mail_id, f"claim expired after {MAX_ATTEMPTS} tries: set to failed")
            else:
                cur = conn.execute("UPDATE screen_mail SET owner=?, claimed_at=?, attempts=attempts+1 "
                                   "WHERE mail_id=? AND state='claimed' AND claimed_at<?", (owner, now, mail_id, cutoff))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return cur.rowcount == 1


def _finish(conn, mail_id, owner, cfg, label, envelope):
    """End a claim: decided with the T1 label, or failed (a shadow failure is final). Only the owner may."""
    state = "decided" if label else "failed"
    conn.execute("UPDATE screen_mail SET state=?, envelope=?, final_label=?, final_by=?, decided_at=? "
                 "WHERE mail_id=? AND owner=? AND state='claimed'",
                 (state, envelope, label, "screener:" + cfg["model"] if label else None, rookery.now(), mail_id, owner))
    conn.commit()


def _log(session, mail_id, cause):
    turn_block.log_event(os.path.expanduser("~/.rookery"), stream="screener", session=session, msg_id=mail_id,
                         outcome="error", reason=cause)


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


def _screen_session(conn, sid, name, cfg, backend, owner, cutoff, stop):
    """Screen up to PER_SESSION mails of one session. A mail that raises is logged and ends as failed; the next
    mail goes on. Returns the number of mails taken. The lease (lease_s, at least twice timeout_s by
    screener_config.check) is longer than one decision, so the owner is not rechecked before a write."""
    taken = 0
    oldest = rookery.now() - cfg["max_age_s"]
    for row in queue(conn, sid, cutoff, limit=PER_SESSION, oldest=oldest):
        if stop:
            break
        mail_id = row["id"]
        if not _claim(conn, mail_id, sid, owner, cutoff):
            continue
        taken += 1
        env = label = None
        try:
            env = build_envelope(conn, conn.execute("SELECT * FROM inbox WHERE id=?", (mail_id,)).fetchone(), cfg)
        except Exception:
            pass   # screen_one writes the bad-envelope record
        try:
            label = screen_one(conn, mail_id, cfg, backend)
        except Exception as e:   # a poison mail: log it, end the claim as failed, go on
            conn.rollback()
            _log(name, mail_id, f"{type(e).__name__}: {e}")
        _finish(conn, mail_id, owner, cfg, label, env)
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
                            taken += _screen_session(conn, sid, name, cfg, backend, owner, cutoff, stop)
                        except Exception as e:
                            conn.rollback()
                            _log(name, None, f"{type(e).__name__}: {e}")
                            code = 2
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
