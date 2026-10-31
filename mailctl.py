#!/usr/bin/env python3
"""Rookery mailctl — stdlib HTTP client for a remote mailroom sidecar. Lets a
machine with NO local DB (e.g. the Mac Studio) join the mesh over the LAN.
Copy just this one file to the peer; it needs nothing but Python 3.

  python3 mailctl.py send  --url http://HOST:8765 --to X --from me --body "hi"
  python3 mailctl.py inbox --url http://HOST:8765 --node X
  python3 mailctl.py loop  --url http://HOST:8765 --node X    # a remote mock node
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("ROOKERY_TOKEN")  # overridden by --token


def _ctx(url):
    # ROOKERY_INSECURE_TLS=1 skips cert verification for a self-signed sidecar
    # on a TRUSTED LAN. For the internet, use a CA-signed cert or a tunnel.
    if url.startswith("https") and os.environ.get("ROOKERY_INSECURE_TLS"):
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib would carry the Authorization header to the new host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused ({code})", headers, fp)


def call(url, path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers=headers)
    handlers = [_NoRedirect()]
    ctx = _ctx(url)
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    with urllib.request.build_opener(*handlers).open(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def log(node, m):
    print(f"[{time.strftime('%H:%M:%S')}] ({node}) {m}", flush=True)


def loop(url, node, poll):
    call(url, "/register", "POST", {"node_id": node, "kind": "remote"})
    log(node, f"online via {url} (remote mock node). watching for mail…")
    while True:
        call(url, "/heartbeat", "POST", {"node_id": node})
        msgs = call(url, "/inbox?node=" + node, "GET")["messages"]
        if not msgs:
            time.sleep(poll)
            continue
        ids = [m["id"] for m in msgs]
        call(url, "/claim", "POST", {"node": node, "ids": ids})
        for m in msgs:
            log(node, f"got mail from {m['sender']}: {m['body'][:60]}")
            if not m["body"].startswith("ACK:") and m["sender"] != node:
                call(url, "/send", "POST", {
                    "sender": node, "recipient": m["sender"],
                    "body": f'ACK: {node} (remote) handled "{m["body"][:40]}"',
                })
        call(url, "/ack", "POST", {"node": node, "ids": ids})


def main():
    ap = argparse.ArgumentParser(description="Rookery remote HTTP client.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="send one message")
    s.add_argument("--url", required=True)
    s.add_argument("--to", required=True, dest="to")
    s.add_argument("--from", default="human", dest="frm")
    s.add_argument("--body", required=True)
    s.add_argument("--topic", default=None)
    i = sub.add_parser("inbox", help="legacy: list mail to a node",
                       description="Legacy command. Lists mail to a node.")
    i.add_argument("--url", required=True)
    i.add_argument("--node", required=True)
    lp = sub.add_parser("loop", help="legacy: run a remote mock node",
                        description="Legacy command. Runs a remote mock node.")
    lp.add_argument("--url", required=True)
    lp.add_argument("--node", required=True)
    lp.add_argument("--poll", type=float, default=1.0)
    for p in (s, i, lp):
        p.add_argument("--token", default=None, help="Bearer token (default $ROOKERY_TOKEN)")
    a = ap.parse_args()
    global TOKEN
    if getattr(a, "token", None):
        TOKEN = a.token

    try:
        run(a)
    except urllib.error.HTTPError as e:
        print(_http_error_text(e), file=sys.stderr)
        sys.exit(2 if e.code in (400, 403) else 1)


def _http_error_text(e):
    """The server's `error` and `detail`; the HTTP reason when the body is not JSON."""
    try:
        body = json.loads(e.read())
        err, detail = body["error"], body.get("detail")
    except (ValueError, KeyError, TypeError, AttributeError):
        return f"HTTP {e.code}: {e.reason}"
    return f"{err}: {detail}" if detail else str(err)


def run(a):
    if a.cmd == "send":
        print(call(a.url, "/send", "POST",
                   {"sender": a.frm, "recipient": a.to, "body": a.body, "topic": a.topic}))
    elif a.cmd == "inbox":
        for m in call(a.url, "/inbox?node=" + a.node, "GET")["messages"]:
            print(m["id"], m["sender"], "->", m["recipient"], ":", m["body"][:70])
    elif a.cmd == "loop":
        try:
            loop(a.url, a.node, a.poll)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
