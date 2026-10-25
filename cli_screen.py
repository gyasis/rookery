"""cli_screen -- `rookery screen run|status|review|mark|report|export|release|quarantine|escalate` (specs/004-screener-chain, contracts/screener.md).

Register with: cli_screen.add_parser(subparsers)
Exit codes: 0 ok, 2 bad input or bad configuration (stderr names the item), 130 stopped with Ctrl-C.
`screener` is imported only inside the handlers: it can pull in PromptChain (6 s, F20), and rookery_cli
imports this module on every call, hooks included.
"""

import getpass
import json
import math
import os
import sys
import tempfile
import time
import urllib.request

import rookery
import screener_t0
import turn_block

EXIT_BAD = 2


def _fail(msg):
    print(f"rookery: {msg}", file=sys.stderr)
    sys.exit(EXIT_BAD)


def _config():
    import screener_config
    try:
        cfg = screener_config.load()
        screener_config.check(cfg)
    except ValueError as e:
        _fail(str(e))
    return cfg


def cmd_run(args):
    import screener
    cfg = _config()
    if args.poll <= 0:
        _fail(f"--poll: must be above 0, got {args.poll}")
    if args.session:   # a bad name stops here, before the startup line
        conn = screener.rookery.connect(screener.turn_block.db_path())
        try:
            screener.open_sessions(conn, [args.session])
        except ValueError as e:
            _fail(str(e))
        finally:
            conn.close()
    modes = " ".join(f"{k}={v}" for k, v in cfg["checks"].items())
    print(f"screener backend={screener.backend_path()} python={sys.executable} base_url={cfg['base_url']} "
          f"model={cfg['model']} {modes}", flush=True)
    try:
        code = screener.run(sessions=[args.session] if args.session else None, once=args.once, poll=args.poll)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(code)


def _host_answers(base_url):
    """True when GET {base_url}/v1/models answers (a metadata call, not a model call)."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/v1/models", timeout=5) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def cmd_status(args):
    import screener
    cfg = _config()
    conn = screener.rookery.connect(screener.turn_block.db_path())
    try:
        try:
            sessions = screener.open_sessions(conn, [args.session] if args.session else None)
        except ValueError as e:
            _fail(str(e))
        now = screener.rookery.now()
        cutoff = now - cfg["lease_s"]
        rows = []
        for sid, name in sessions:
            q = screener.queue(conn, sid, cutoff, oldest=now - cfg["max_age_s"])
            counts = dict(conn.execute("SELECT state, COUNT(*) FROM screen_mail WHERE session_id=? GROUP BY state",
                                       (sid,)).fetchall())
            rows.append({"session": name, "backlog": len(q),
                         "oldest_age_s": max(0, now - q[0]["created_at"]) if q else None,
                         "escalated": counts.get("escalated", 0), "failed": counts.get("failed", 0),
                         "decided": counts.get("decided", 0)})
    finally:
        conn.close()
    out = {"backend": screener.backend_path(), "python": sys.executable, "model": cfg["model"],
           "host": {"url": cfg["base_url"], "answers": _host_answers(cfg["base_url"]), "checked_at": int(time.time())},
           "checks": cfg["checks"], "sessions": rows}
    if args.json:
        print(json.dumps(out))
        return
    print(f"backend: {out['backend']}\npython: {out['python']}\nmodel: {out['model']}")
    print(f"host: {out['host']['url']} answers={out['host']['answers']}")
    for k, v in out["checks"].items():
        print(f"check {k}: {v}")
    for r in rows:
        print(f"session {r['session']}: backlog={r['backlog']} oldest_age_s={r['oldest_age_s']} "
              f"escalated={r['escalated']} failed={r['failed']} decided={r['decided']}")


T1_LABELS = ("deliver", "wrong-recipient", "task-already-done", "unneeded")   # as screener.LABELS
MODEL_CHECKS = ("t1", "hunch", "t2")
LIVE_MIN = 150      # compared decisions before a model check may go live (FR-017)

# The newest call of each decision counts (D20).
_NEWEST = ("JOIN screen_calls c ON c.id=(SELECT MAX(id) FROM screen_calls WHERE decision_id=d.id)")


def _db():
    return rookery.connect(turn_block.db_path())


def _by(args):
    try:
        return screener_t0.human_by(args.by or getpass.getuser())
    except ValueError as e:
        _fail(str(e))


def _check_arg(check):
    if check is not None and check not in screener_t0.CHECK_NAMES:
        _fail(f"--check: {check!r} is not one of {', '.join(screener_t0.CHECK_NAMES)}")


def cmd_review(args):
    """List the decisions with no call. Never a mail body (peek shows it)."""
    _check_arg(args.check)
    if args.limit < 1:
        _fail(f"--limit: must be 1 or more, got {args.limit}")
    sql = ("SELECT d.id, d.mail_id, d.check_name, d.label, d.confidence, i.sender, i.recipient, i.topic, i.thread"
           " FROM screen_decisions d LEFT JOIN inbox i ON i.id=d.mail_id"
           " WHERE d.label IS NOT NULL AND d.label != 'skipped' AND NOT EXISTS (SELECT 1 FROM screen_calls c WHERE c.decision_id=d.id)")
    params = []
    if args.check:
        sql += " AND d.check_name=?"
        params.append(args.check)
    if args.session:
        conn = _db()
        try:
            known = conn.execute("SELECT 1 FROM rookery_sessions WHERE name=? OR session_id=?",
                                 (args.session, args.session)).fetchone()
        finally:
            conn.close()
        if known is None:
            _fail(f"session {args.session}: no such session")
        sql += " AND d.session_id IN (SELECT session_id FROM rookery_sessions WHERE name=? OR session_id=?)"
        params += [args.session, args.session]
    conn = _db()
    try:
        rows = conn.execute(sql + " ORDER BY d.id LIMIT ?", (*params, args.limit)).fetchall()
    finally:
        conn.close()
    out = [{"decision": r[0], "mail_id": r[1], "check": r[2], "label": r[3], "confidence": r[4],
            "from": r[5], "to": r[6], "task": r[7], "thread": r[8]} for r in rows]
    if args.json:
        print(json.dumps(out))
        return
    if not out:
        print("No decision waits for a call.")
    for r in out:
        print(f"decision {r['decision']} mail {r['mail_id']} {r['check']} label={r['label']} "
              f"confidence={r['confidence']} from={r['from']} to={r['to']} task={r['task']} thread={r['thread']}")


def _check_label(conn, d, label):
    """A model call must name a label that the decision can have. A suggest call names an agent of the session."""
    if d[2] == "suggest":
        roster = [r[0] for r in conn.execute("SELECT name FROM identities WHERE session_id=?", (d[4],))]
        if label != "none" and label not in roster:
            _fail(f"decision {d[0]}: {label!r} is not an agent of this session. Use one of "
                  f"{', '.join(roster + ['none'])}.")
    else:
        allowed = T1_LABELS + rookery.QUARANTINE_REASONS
        if label not in allowed:
            _fail(f"decision {d[0]}: {label!r} is not a label. Use one of {', '.join(allowed)}.")


def cmd_mark(args):
    """One user call for one decision (data-model mapping). T0 takes right or wrong, a model takes right or a label."""
    what = args.call
    if not what.strip():
        _fail(f"decision {args.decision}: the call is empty. Give right, wrong or a label.")
    by = _by(args)
    conn = _db()
    try:
        d = conn.execute("SELECT id, mail_id, stage, label, session_id FROM screen_decisions WHERE id=?",
                         (args.decision,)).fetchone()
        if d is None:
            _fail(f"decision {args.decision}: no such decision")
        if d[1] is None:
            _fail(f"decision {args.decision}: This decision has no mail. Rookery cannot record a call for it.")
        if d[3] is None:
            _fail(f"decision {args.decision}: it has no label, so it has nothing to compare")
        if d[3] == "skipped":
            _fail(f"decision {args.decision}: the decision is skipped. A skipped decision has no answer to compare.")
        if d[2] == "t0":
            if what not in ("right", "wrong"):
                _fail(f"decision {args.decision} is a T0 record. Give right or wrong, not a label.")
            label, verdict = (d[3], "right") if what == "right" else ("deliver", "wrong")
        elif what == "wrong":
            _fail(f"decision {args.decision} is a model decision. Give the right label instead of wrong.")
        else:
            if what != "right":
                _check_label(conn, d, what)
            label = d[3] if what == "right" else what
            verdict = "right" if label == d[3] else "wrong"
        screener_t0.add_call(conn, d[0], d[1], label, verdict, "mark", by)
        conn.commit()
    finally:
        conn.close()
    print(f"decision {args.decision}: call {label} is {verdict}")


def _check_row(rows, cfg, name):
    compared = len(rows)
    agree = sum(1 for r in rows if r["label"] == r["call_label"])
    pairs, steps = {}, {}
    for r in rows:
        if r["label"] != r["call_label"]:
            k = f"{r['label']}->{r['call_label']}"
            pairs[k] = pairs.get(k, 0) + 1
        if r["confidence"] is not None:
            b = math.floor(round(r["confidence"] * 10, 6)) / 10
            n, a = steps.get(f"{b:.1f}", (0, 0))
            steps[f"{b:.1f}"] = (n + 1, a + (r["label"] == r["call_label"]))
    ready, missing = True, 0
    if name in MODEL_CHECKS:
        missing = max(0, LIVE_MIN - compared)
        ready = missing == 0 and (name != "t1" or cfg["bands"].get("t1") is not None)
    return {"compared": compared, "agree": agree, "rate": round(agree / compared, 3) if compared else None,
            "disagreements": pairs,
            "by_confidence": {k: {"compared": n, "rate": round(a / n, 3)} for k, (n, a) in sorted(steps.items())},
            "live_ready": ready, "missing": missing}


def _compared(conn, check):
    sql = ("SELECT d.id, d.mail_id, d.session_id, d.check_name, d.label, d.confidence, d.decided_by,"
           " c.call_label, c.\"by\" AS call_by, c.created_at AS called_at FROM screen_decisions d " + _NEWEST)
    sql += " WHERE d.label IS NOT NULL AND d.label != 'skipped'"
    if check:
        return conn.execute(sql + " AND d.check_name=? ORDER BY d.id", (check,)).fetchall()
    return conn.execute(sql + " ORDER BY d.id").fetchall()


def cmd_report(args):
    _check_arg(args.check)
    cfg = _config()
    conn = _db()
    try:
        rows = _compared(conn, args.check)
    finally:
        conn.close()
    by_check = {}
    for r in rows:
        by_check.setdefault(r["check_name"], []).append(r)
    if args.check:
        by_check.setdefault(args.check, [])
    out = {"checks": {k: _check_row(v, cfg, k) for k, v in sorted(by_check.items())}}
    if args.json:
        print(json.dumps(out))
        return
    if not rows:
        print("No decision has a call yet.")
    for k, v in out["checks"].items():
        print(f"check {k}: compared={v['compared']} agree={v['agree']} rate={v['rate']} "
              f"live_ready={v['live_ready']} missing={v['missing']}")
        for pair, n in v["disagreements"].items():
            print(f"  disagreement {pair}: {n}")
        for step, s in v["by_confidence"].items():
            print(f"  confidence {step}: compared={s['compared']} rate={s['rate']}")


def cmd_export(args):
    """One JSON line for each compared decision, with the envelope and no body (FR-018)."""
    _check_arg(args.check)
    conn = _db()
    try:
        rows = _compared(conn, args.check)
        names = dict(conn.execute("SELECT session_id, name FROM rookery_sessions").fetchall())
        envs = dict(conn.execute("SELECT mail_id, envelope FROM screen_mail").fetchall())
    finally:
        conn.close()
    lines = []
    for r in rows:
        try:
            env = json.loads(envs.get(r["mail_id"]) or "null")
        except (TypeError, ValueError):
            env = None
        lines.append(json.dumps({
            "mail_id": r["mail_id"], "session": names.get(r["session_id"], r["session_id"]), "check": r["check_name"],
            "envelope": env, "label": r["label"], "confidence": r["confidence"],
            "decided_by": r["decided_by"], "call_label": r["call_label"], "call_by": r["call_by"],
            "called_at": r["called_at"]}))
    text = "".join(x + "\n" for x in lines)
    if args.out:
        folder = os.path.dirname(os.path.abspath(args.out))
        if not os.path.isdir(folder):
            _fail(f"--out: the folder {folder} does not exist")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=folder, prefix=".export-")
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
            os.replace(tmp, args.out)
        except OSError as e:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            _fail(f"--out {args.out}: {e.strerror or e}")
        print(f"Wrote {len(lines)} lines to {args.out}.")
    else:
        sys.stdout.write(text)


def _act(args, action, reason=None, **kw):
    """Run release or quarantine with its user calls in one transaction (the status change commits both)."""
    by = _by(args)
    conn = _db()
    try:
        screener_t0.calls_for_action(conn, args.mail_id, action, reason, by)
        if action == "release":
            rookery.release(conn, args.mail_id, by)
        else:
            return rookery.quarantine(conn, args.mail_id, reason, by, **kw)
    except ValueError as e:
        conn.rollback()
        _fail(str(e))
    finally:
        conn.close()


def cmd_release(args):
    _act(args, "release")
    print(f"Released mail {args.mail_id}.")


def cmd_quarantine(args):
    _act(args, "quarantine", args.reason, note=args.note, suggest=args.suggest)
    print(f"Quarantined mail {args.mail_id} ({args.reason}).")


def cmd_escalate(args):
    conn = _db()
    try:
        # T014 adds screener_t0.notify_escalated. Until then this only moves the status and sends no notice.
        rookery.escalate(conn, args.mail_id, _by(args), args.reason or "held for a person")
    except ValueError as e:
        _fail(str(e))
    finally:
        conn.close()
    print(f"Escalated mail {args.mail_id}.")


def add_parser(subparsers):
    p = subparsers.add_parser("screen", help="Screen mail with the T1 decision model.")
    sub = p.add_subparsers(dest="screen_command", metavar="<screen_command>")
    sub.required = True
    r = sub.add_parser("run", help="Screen the mail that has no decision yet.")
    r.add_argument("--session", help="Screen only this session (name or ID).")
    r.add_argument("--once", action="store_true", help="Do one pass and exit.")
    r.add_argument("--poll", type=float, default=2.0, help="Seconds between passes (default 2).")
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("status", help="Show the backend, the host and the backlog.")
    s.add_argument("--session", help="Show only this session (name or ID).")
    s.add_argument("--json", action="store_true", help="Print one JSON object.")
    s.set_defaults(func=cmd_status)
    v = sub.add_parser("review", help="List the decisions that have no call.")
    v.add_argument("--session", help="Show only this session (name or ID).")
    v.add_argument("--check", help="Show only this check.")
    v.add_argument("--limit", type=int, default=50, help="Rows to show (default 50).")
    v.add_argument("--json", action="store_true", help="Print a JSON list.")
    v.set_defaults(func=cmd_review)
    m = sub.add_parser("mark", help="Say if a decision was right.")
    m.add_argument("decision", type=int, metavar="DECISION_ID")
    m.add_argument("call", metavar="right|wrong|LABEL")
    m.add_argument("--by", help="Your name (default: your user name).")
    m.set_defaults(func=cmd_mark)
    rp = sub.add_parser("report", help="Show how often the calls agree with the decisions.")
    rp.add_argument("--check", help="Show only this check.")
    rp.add_argument("--json", action="store_true", help="Print one JSON object.")
    rp.set_defaults(func=cmd_report)
    e = sub.add_parser("export", help="Write one JSON line for each compared decision.")
    e.add_argument("--check", help="Export only this check.")
    e.add_argument("--out", help="Write to this file (default: standard output).")
    e.set_defaults(func=cmd_export)
    rl = sub.add_parser("release", help="Deliver a held or escalated mail.")
    rl.add_argument("mail_id", type=int, metavar="MAIL_ID")
    rl.add_argument("--by", help="Your name (default: your user name).")
    rl.set_defaults(func=cmd_release)
    q = sub.add_parser("quarantine", help="Reject a held or escalated mail.")
    q.add_argument("mail_id", type=int, metavar="MAIL_ID")
    q.add_argument("reason", choices=rookery.QUARANTINE_REASONS, metavar="REASON")
    q.add_argument("--note", help="A note for the sender.")
    q.add_argument("--suggest", help="The agent that the sender should use.")
    q.add_argument("--by", help="Your name (default: your user name).")
    q.set_defaults(func=cmd_quarantine)
    x = sub.add_parser("escalate", help="Hold a mail for a person.")
    x.add_argument("mail_id", type=int, metavar="MAIL_ID")
    x.add_argument("--reason", help="Why the mail waits.")
    x.add_argument("--by", help="Your name (default: your user name).")
    x.set_defaults(func=cmd_escalate)
