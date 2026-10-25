"""cli_screen -- `rookery screen run|status` (specs/004-screener-chain, contracts/screener.md).

Register with: cli_screen.add_parser(subparsers)
Exit codes: 0 ok, 2 bad input or bad configuration (stderr names the item), 130 stopped with Ctrl-C.
`screener` is imported only inside the handlers: it can pull in PromptChain (6 s, F20), and rookery_cli
imports this module on every call, hooks included.
"""

import json
import os
import sys
import time
import urllib.request

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
