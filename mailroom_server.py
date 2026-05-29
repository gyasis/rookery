#!/usr/bin/env python3
"""Rookery mailroom sidecar — exposes the SQLite mailroom over HTTP so nodes on
OTHER machines (e.g. the Mac Studio) can join the mesh over the LAN. Stdlib only.

  python3 mailroom_server.py --host 0.0.0.0 --port 8765

Run it on the box that holds the DB; bind 0.0.0.0 so peers reach it. Peers use
mailctl.py (or any HTTP client). NEVER put the SQLite file on a network share —
locking is broken; peers talk to THIS service over TCP instead.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import rookery as R


def _rows(rs):
    return [dict(r) for r in rs]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _reply(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        c = R.connect()
        if u.path == "/health":
            return self._reply({"ok": True, "db": R.DB_PATH})
        if u.path == "/inbox":
            return self._reply({"messages": _rows(R.fetch_undelivered(c, q["node"][0]))})
        if u.path == "/thread":
            return self._reply({"messages": _rows(R.fetch_thread(c, q["node"][0]))})
        self._reply({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        d = self._json_body()
        c = R.connect()
        if u.path == "/send":
            mid = R.send(c, d["sender"], d["recipient"], d["body"], d.get("topic"))
            return self._reply({"id": mid})
        if u.path == "/claim":
            R.claim(c, d["ids"]); return self._reply({"ok": True})
        if u.path == "/ack":
            R.ack(c, d["ids"]); return self._reply({"ok": True})
        if u.path == "/register":
            R.register_node(c, d["node_id"], d.get("kind", "remote"),
                            d.get("pid"), d.get("lifecycle", "ephemeral"))
            return self._reply({"ok": True})
        if u.path == "/heartbeat":
            R.heartbeat(c, d["node_id"]); return self._reply({"ok": True})
        if u.path == "/status":
            R.set_status(c, d["node_id"], d["status"]); return self._reply({"ok": True})
        if u.path == "/request_credential":
            rid = R.request_credential(c, d["node_id"], d["resource"])
            return self._reply({"id": rid})
        self._reply({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="Rookery mailroom HTTP sidecar.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    R.connect()  # ensure DB + schema exist
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[mailroom] serving {R.DB_PATH} on http://{a.host}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
