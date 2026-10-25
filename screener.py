#!/usr/bin/env python3
"""The T1 screener (004 T010; research D8, D10, D11). build_envelope() makes the state text of one mail.
screen_one() asks the decision model about it and writes the records.

Nothing here changes the inbox: this task only records. Records are written through screener_t0.record and
committed at once. The backend is passed in, so a test uses screener_backend.fake(). Never imports promptchain.
"""
import json
import re
import sqlite3

import screener_backend
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
