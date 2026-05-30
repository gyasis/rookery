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
    import json, os, secrets, socket, ssl, sys, time, urllib.error, urllib.request
    import discovery, identity, known_hosts, config_manager

    # ------------------------------------------------------------------ #
    # 1. Resolve target mailroom
    # ------------------------------------------------------------------ #
    if args.url:
        url = args.url.rstrip("/")
    else:
        mdns = discovery.discover_mailrooms(timeout=2.0)
        udp  = discovery.listen(timeout=2.0)
        # Dedupe by (host, port)
        seen, combined = set(), []
        for e in mdns + udp:
            key = (e["host"], e["port"])
            if key not in seen:
                seen.add(key)
                combined.append(e)
        if not combined:
            print(
                "No mailroom found on the LAN. "
                "Pass `rookery up <url>` for an explicit address.",
                file=sys.stderr,
            )
            sys.exit(1)
        elif len(combined) == 1:
            e = combined[0]
            url = f"http://{e['host']}:{e['port']}"
            print(f"Discovered mailroom at {url}")
        else:
            print("Multiple mailrooms found:")
            for i, e in enumerate(combined, 1):
                print(f"  {i}) http://{e['host']}:{e['port']}  ({e.get('name', '')})")
            try:
                choice = input(f"Which one? [1-{len(combined)}]: ").strip()
                idx = int(choice) - 1
                if not (0 <= idx < len(combined)):
                    raise ValueError
            except (ValueError, EOFError):
                print("Invalid selection.", file=sys.stderr)
                sys.exit(1)
            e = combined[idx]
            url = f"http://{e['host']}:{e['port']}"

    # ------------------------------------------------------------------ #
    # 2. SSL context (for https:// URLs with --insecure-tls)
    # ------------------------------------------------------------------ #
    def _ssl_ctx():
        if args.insecure_tls and url.startswith("https://"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def _open(req, timeout=35):
        ctx = _ssl_ctx()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx) if ctx \
               else urllib.request.urlopen(req, timeout=timeout)

    # ------------------------------------------------------------------ #
    # 3. 3-word slug
    # ------------------------------------------------------------------ #
    _WORDS = (
        "apricot blackberry cinnamon dandelion eclipse falcon ginger harbor indigo "
        "jasmine kestrel lantern mango nectar opal pomegranate quail raven sapphire "
        "tangerine umber violet walnut xenon yarrow zinnia amber basil clover delta "
        "ember fjord glacier helix iris jade kelp linden meadow nimbus orchid plum "
        "quartz reed sage thistle ulu vanilla willow xylophone yew zephyr almond "
        "birch cardinal dahlia ember fern gardenia hawthorn ivy juniper kale "
        "larkspur maple"
    ).split()
    slug = "-".join(secrets.choice(_WORDS) for _ in range(3))

    # ------------------------------------------------------------------ #
    # 4. Node identity
    # ------------------------------------------------------------------ #
    node_id = args.node_id or f"peer-{socket.gethostname().replace('.', '-')}"
    identity.load_or_create()
    pubkey_b64 = identity.pubkey_b64()

    # ------------------------------------------------------------------ #
    # 5. POST /join
    # ------------------------------------------------------------------ #
    join_body = json.dumps(
        {"node_id": node_id, "pubkey_b64": pubkey_b64, "slug": slug}
    ).encode()
    req = urllib.request.Request(
        f"{url}/join",
        data=join_body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _open(req, timeout=15) as r:
            join_resp = json.loads(r.read())
    except Exception as e:
        print(f"Failed to reach mailroom at {url}: {e}", file=sys.stderr)
        sys.exit(1)

    request_id = join_resp.get("request_id")
    if not request_id:
        print(f"Unexpected /join response: {join_resp}", file=sys.stderr)
        sys.exit(1)

    print(f"Waiting for approval on the mailroom. Your code:\n  → {slug}\n")

    # ------------------------------------------------------------------ #
    # 6. Long-poll /join/<request_id>
    # ------------------------------------------------------------------ #
    deadline = time.monotonic() + 300  # 5-minute outer timeout
    token = card_pubkey = None
    while time.monotonic() < deadline:
        poll_req = urllib.request.Request(f"{url}/join/{request_id}")
        try:
            with _open(poll_req, timeout=35) as r:
                resp = json.loads(r.read())
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1.0)
            continue

        status = resp.get("status")
        if status == "approved":
            token      = resp.get("token")
            card_pubkey = resp.get("card_pubkey")
            break
        elif status == "denied":
            print("Approval denied.", file=sys.stderr)
            sys.exit(1)
        # "pending" → keep polling

    else:
        print("Timed out waiting for approval (5 min).", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 7. TOFU pin on card_pubkey
    # ------------------------------------------------------------------ #
    if not card_pubkey:
        print("WARN: sidecar is not signing its agent card — TOFU pin skipped.")
    else:
        state = known_hosts.check(url, card_pubkey)
        if state == "changed":
            # Read stored key for the warning
            stored = next(
                (e.pubkey for e in known_hosts.load() if e.host == url), "<not found>"
            )
            print(
                "\n"
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                f"The Ed25519 card public key for {url} does NOT match the one\n"
                f"stored in ~/.rookery/known_hosts.\n"
                f"  expected: {stored}\n"
                f"  got:      {card_pubkey}\n"
                "Someone could be eavesdropping (man-in-the-middle attack).\n"
                "If you trust the new key, remove the old line from ~/.rookery/known_hosts\n"
                "and re-run `rookery up`.",
                file=sys.stderr,
            )
            sys.exit(1)
        elif state == "unknown":
            known_hosts.add(url, card_pubkey, label=node_id)
            print(f"Pinned card pubkey for {url} (first contact).")
        else:  # 'ok'
            print(f"Pinned card pubkey for {url} matches known_hosts.")

    # ------------------------------------------------------------------ #
    # 8. Merge ~/.claude.json
    # ------------------------------------------------------------------ #
    here = os.path.dirname(os.path.abspath(__file__))
    mcp_path = os.path.join(here, "rookery_mcp.py")
    env = {"ROOKERY_NODE": node_id, "ROOKERY_URL": url, "ROOKERY_TOKEN": token or ""}
    if args.insecure_tls:
        env["ROOKERY_INSECURE_TLS"] = "1"
    r = config_manager.merge_mcp_entry("rookery", "python3", [mcp_path], env)
    print(
        f"~/.claude.json: {'updated' if r['written'] else 'unchanged'}"
        + (f"  (backup: {r['backup']})" if r["backup"] else "")
    )

    # ------------------------------------------------------------------ #
    # 9. Success block
    # ------------------------------------------------------------------ #
    print(
        "\n"
        "════════════════════════════════════════════════════════════════\n"
        f"✓ joined mesh as `{node_id}`  →  {url}\n"
        "✓ ~/.claude.json updated with the `rookery` MCP server\n"
        "\n"
        "Restart Claude Code, then paste this as your FIRST message:\n"
        "\n"
        f"  You are node `{node_id}` in a Rookery mesh. Use the `rookery`\n"
        "  MCP server. Loop: call `rookery.await_message(timeout=300)`\n"
        "  and react to anything it returns. Use `rookery.send(to, body)`\n"
        "  for replies. Continue until told to stop.\n"
        "\n"
        "════════════════════════════════════════════════════════════════"
    )


def cmd_approve(args):
    import json, sys, time, urllib.request
    base = args.url.rstrip("/")
    token = args.token or ""

    def _get(path):
        req = urllib.request.Request(base + path,
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    try:
        pend = _get("/pending").get("pending", [])
    except Exception as e:
        print(f"could not reach {base}/pending: {e}", file=sys.stderr)
        sys.exit(1)

    if not pend:
        print("No pending join requests.")
        return

    print(f"{len(pend)} pending request(s):")
    for i, e in enumerate(pend, 1):
        age = int(time.time() - e.get("requested_at", time.time()))
        print(f"  {i}) node={e['node_id']:<24} slug={e['slug']:<24} "
              f"pubkey={e['pubkey_b64'][:24]}...  ({age}s ago)")
    print()

    for i, e in enumerate(pend, 1):
        try:
            choice = input(f"Approve {i}/{len(pend)} (node={e['node_id']}, slug={e['slug']})? [y/N/s=skip]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if choice in ("s", "skip"):
            continue
        decision = "approve" if choice in ("y", "yes") else "deny"
        try:
            out = _post("/approve", {"request_id": e["request_id"], "decision": decision})
            tok = (out.get("token") or "")[:16]
            print(f"  -> {decision} ({out.get('status')}, token={tok}...)" if tok
                  else f"  -> {decision} ({out.get('status')})")
        except Exception as err:
            print(f"  -> error: {err}", file=sys.stderr)


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
