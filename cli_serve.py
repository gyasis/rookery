"""cli_serve — `rookery serve` subcommand implementation."""

import sys


def _ansi(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stderr.isatty() else text


def _prompt_and_approve(args, entry):
    """Flash the approval prompt to the controlling terminal; on 'y' call
    the local /approve endpoint."""
    import json
    import urllib.request

    slug = entry["slug"]
    node_id = entry["node_id"]
    pubkey_short = entry["pubkey_b64"][:24]

    if sys.stderr.isatty():
        tag = _ansi("1;36", "[ROOKERY]")
        slug_line = "  " + _ansi("1;93", f"▶  {slug}  ◀")
        border_h = "═" * 62
        border_top = f"╔{border_h}╗"
        border_bot = f"╚{border_h}╝"
        border_mid = "║"
        block = (
            f"\n{border_top}\n"
            f"{border_mid}  {tag} Peer wants to join the mesh.\n"
            f"{border_mid}    node id : {node_id}\n"
            f"{border_mid}    pubkey  : {pubkey_short}...\n"
            f"{border_mid}    slug    :\n"
            f"{border_mid}{slug_line}\n"
            f"{border_bot}\n"
            f"  {_ansi('1', 'Does the joining machine show this code?  Approve? [y/N]')} "
        )
    else:
        block = (
            "\n"
            "[ROOKERY] Peer wants to join the mesh.\n"
            f"          node id : {node_id}\n"
            f"          pubkey  : {pubkey_short}...\n"
            f"          slug    : {slug}\n"
            "  Does the joining machine show this code? Approve? [y/N] "
        )

    sys.stderr.write(block)
    sys.stderr.flush()

    try:
        line = sys.stdin.readline().strip().lower()
    except Exception:
        line = ""

    decision = "approve" if line in ("y", "yes") else "deny"
    body = json.dumps(
        {"request_id": entry["request_id"], "decision": decision}
    ).encode()
    req = urllib.request.Request(
        f"http://{args.host}:{args.port}/approve",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {args.token or ''}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read())
        print(
            f"[ROOKERY] -> {decision} ({out.get('status')}, token: "
            f"{(out.get('token') or '')[:16]}...)",
            flush=True,
        )
    except Exception as e:
        print(f"[ROOKERY] approve failed: {e}", flush=True)


def cmd_serve(args):
    import threading
    import time

    import discovery
    import handshake
    import identity
    import mailroom_server
    import security
    from http.server import ThreadingHTTPServer

    # Seed module-level globals mailroom_server expects before we start it.
    mailroom_server.PUBLIC_URL = args.public_url or f"http://{args.host}:{args.port}"
    mailroom_server.DEFAULT_NODE = "mailroom"
    mailroom_server.CARD_KEY = args.card_key
    security.get_policy(token=args.token)  # seed policy with token if DefaultPolicy
    import rookery as R

    R.connect()

    # Start sidecar in a daemon thread (it owns its own server.serve_forever()).
    srv = ThreadingHTTPServer((args.host, args.port), mailroom_server.Handler)
    srv_thread = threading.Thread(target=srv.serve_forever, daemon=True, name="sidecar")
    srv_thread.start()
    print(
        f"[rookery serve] mailroom on http://{args.host}:{args.port}  (Ctrl-C to stop)",
        flush=True,
    )

    # Discovery announce — UDP broadcast on the side, with our pubkey embedded.
    identity.load_or_create()
    pk = identity.pubkey_b64()
    stop_discovery = threading.Event()
    threading.Thread(
        target=discovery.announce_loop,
        kwargs={
            "port": args.port,
            "name": "mailroom",
            "pubkey_b64": pk,
            "interval": 5.0,
            "stop_event": stop_discovery,
        },
        daemon=True,
        name="announce",
    ).start()

    # Approval prompt watcher — polls handshake.PENDING every second.
    seen: set = set()

    def watcher():
        while True:
            try:
                for entry in handshake.list_pending():
                    rid = entry["request_id"]
                    if rid in seen:
                        continue
                    seen.add(rid)
                    _prompt_and_approve(args, entry)
            except Exception as e:
                print(f"[approval watcher] {e}", file=sys.stderr, flush=True)
            time.sleep(1.0)

    threading.Thread(target=watcher, daemon=True, name="approval-watcher").start()

    try:
        srv_thread.join()
    except KeyboardInterrupt:
        print("\n[rookery serve] stopping...", flush=True)
        stop_discovery.set()
        srv.shutdown()
