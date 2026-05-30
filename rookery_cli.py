#!/usr/bin/env python3
"""Rookery CLI — unified entrypoint for the Rookery agent-mesh sidecar.

  rookery_cli.py serve   [--host] [--port] [--public-url] [--card-key] [--token]
  rookery_cli.py up      [url]    [--node-id] [--insecure-tls]
  rookery_cli.py approve [--url]  [--token]
"""
import argparse
import os


# ---------------------------------------------------------------------------
# Subcommand handlers (stubs — implementation in later waves)
# ---------------------------------------------------------------------------

def cmd_serve(args):
    import threading, sys, time
    import identity, discovery, handshake, mailroom_server, security
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


def _prompt_and_approve(args, entry):
    """Flash the approval prompt to the controlling terminal; on 'y' call
    the local /approve endpoint."""
    import sys, json, urllib.request

    sys.stderr.write(
        "\n"
        "[ROOKERY] Peer wants to join the mesh.\n"
        f"          node id : {entry['node_id']}\n"
        f"          pubkey  : {entry['pubkey_b64'][:24]}...\n"
        f"          slug    : {entry['slug']}\n"
        "  Does the joining machine show this code? Approve? [y/N] "
    )
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


def cmd_up(args):
    print("up:", vars(args))


def cmd_approve(args):
    print("approve:", vars(args))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="rookery_cli",
        description="Rookery agent-mesh CLI.",
    )
    sub = ap.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # --- serve ---
    p_serve = sub.add_parser("serve", help="Run the mailroom HTTP sidecar.")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8765, help="Bind port (default 8765)")
    p_serve.add_argument("--public-url", default=None,
                         help="How peers reach this sidecar (used in the A2A Agent Card)")
    p_serve.add_argument("--card-key", default=None,
                         help="Ed25519 private-key PEM — signs the agent card")
    p_serve.add_argument("--token", default=os.environ.get("ROOKERY_TOKEN"),
                         help="Bearer token for non-public endpoints (default $ROOKERY_TOKEN)")
    p_serve.set_defaults(func=cmd_serve)

    # --- up ---
    p_up = sub.add_parser("up", help="Bring a peer node online via a remote mailroom.")
    p_up.add_argument("url", nargs="?", default=None,
                      help="Mailroom URL (e.g. http://host:8765); defaults to $ROOKERY_URL")
    p_up.add_argument("--node-id", default=None, help="Node identifier to register")
    p_up.add_argument("--insecure-tls", action="store_true",
                      help="Skip TLS certificate verification (trusted LAN only)")
    p_up.set_defaults(func=cmd_up)

    # --- approve ---
    p_approve = sub.add_parser("approve", help="Approve a pending credential request.")
    p_approve.add_argument("--url", default="http://localhost:8765",
                           help="Mailroom URL (default http://localhost:8765)")
    p_approve.add_argument("--token", default=os.environ.get("ROOKERY_TOKEN"),
                           help="Bearer token (default $ROOKERY_TOKEN)")
    p_approve.set_defaults(func=cmd_approve)

    return ap


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    ap = build_parser()
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
