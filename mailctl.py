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
import time
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


def call(url, path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=_ctx(url)) as r:
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
        call(url, "/claim", "POST", {"ids": ids})
        for m in msgs:
            log(node, f"got mail from {m['sender']}: {m['body'][:60]}")
            if not m["body"].startswith("ACK:") and m["sender"] != node:
                call(url, "/send", "POST", {
                    "sender": node, "recipient": m["sender"],
                    "body": f'ACK: {node} (remote) handled "{m["body"][:40]}"',
                })
        call(url, "/ack", "POST", {"ids": ids})


def main():
    ap = argparse.ArgumentParser(description="Rookery remote HTTP client.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--url", required=True)
    s.add_argument("--to", required=True, dest="to")
    s.add_argument("--from", default="human", dest="frm")
    s.add_argument("--body", required=True)
    s.add_argument("--topic", default=None)
    i = sub.add_parser("inbox")
    i.add_argument("--url", required=True)
    i.add_argument("--node", required=True)
    lp = sub.add_parser("loop")
    lp.add_argument("--url", required=True)
    lp.add_argument("--node", required=True)
    lp.add_argument("--poll", type=float, default=1.0)
    for p in (s, i, lp):
        p.add_argument("--token", default=None, help="Bearer token (default $ROOKERY_TOKEN)")
    a = ap.parse_args()
    global TOKEN
    if getattr(a, "token", None):
        TOKEN = a.token

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
