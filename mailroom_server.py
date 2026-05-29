#!/usr/bin/env python3
"""Rookery mailroom sidecar — exposes the SQLite mailroom over HTTP so nodes on
OTHER machines (e.g. the Mac Studio) can join the mesh over the LAN. Stdlib only.

  python3 mailroom_server.py --host 0.0.0.0 --port 8765

Run it on the box that holds the DB; bind 0.0.0.0 so peers reach it. Peers use
mailctl.py (or any HTTP client). NEVER put the SQLite file on a network share —
locking is broken; peers talk to THIS service over TCP instead.
"""
import argparse
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import rookery as R

# Set from CLI in main(). PUBLIC_URL is what goes in the Agent Card (how peers
# reach us); DEFAULT_NODE routes A2A messages with no explicit recipient;
# TOKEN (if set) is the required Bearer credential for non-public endpoints.
PUBLIC_URL = "http://localhost:8765"
DEFAULT_NODE = "mailroom"
TOKEN = None

# Public (no auth): discovery + liveness. Everything else needs the token.
OPEN_PATHS = {"/health", "/.well-known/agent-card.json", "/.well-known/agent.json"}


def _rows(rs):
    return [dict(r) for r in rs]


# --- A2A (Agent2Agent) interop -------------------------------------------
def _a2a_text(parts):
    """Pull text out of A2A message parts (v0.3 `{kind:text,text}` and v1.0
    `{text,mediaType}` both carry a `text` field)."""
    return "\n".join(p["text"] for p in (parts or []) if isinstance(p, dict) and p.get("text"))


def agent_card(conn):
    """A2A Agent Card — published at /.well-known/agent-card.json so external,
    cross-vendor agents can DISCOVER Rookery. Each registered node is a skill;
    route to one with message metadata.recipient=<node>."""
    skills = []
    for n in conn.execute("SELECT node_id, kind, lifecycle FROM nodes ORDER BY node_id").fetchall():
        skills.append({
            "id": n["node_id"],
            "name": n["node_id"],
            "description": f"Rookery {n['lifecycle'] or 'node'} ({n['kind'] or 'node'}); "
                           f"route with metadata.recipient='{n['node_id']}'.",
            "tags": ["rookery", n["kind"] or "node"],
            "examples": [f"Send a task to {n['node_id']}"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        })
    if not skills:
        skills.append({
            "id": "mailroom", "name": "Rookery mailroom",
            "description": "Send a message into the Rookery mesh (metadata.recipient selects the node).",
            "tags": ["rookery"], "examples": ["hello"],
            "inputModes": ["text/plain"], "outputModes": ["text/plain"],
        })
    card = {
        "protocolVersion": "0.3.0",
        "name": "Rookery Mesh",
        "description": "A local-first agent mesh (SQLite mailroom). Send a task via message/send; "
                       "route to a node with message metadata.recipient.",
        "url": f"{PUBLIC_URL}/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "provider": {"organization": "Rookery", "url": PUBLIC_URL},
        "capabilities": {"streaming": True, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
    }
    if TOKEN:
        card["securitySchemes"] = {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        card["security"] = [{"bearerAuth": []}]
    return card


_A2A_STATE = {"pending": "submitted", "inflight": "working", "done": "completed"}


def a2a_rpc(conn, rpc):
    """Handle one A2A JSON-RPC request. Supports message/send and tasks/get."""
    rid = rpc.get("id")
    method = rpc.get("method")
    params = rpc.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if method == "message/send":
        msg = params.get("message") or {}
        meta = msg.get("metadata") or params.get("metadata") or {}
        recipient = meta.get("recipient") or DEFAULT_NODE
        # colon-free sender id: ':' is the MAILTO:<node>:<text> delimiter, so an
        # "a2a:client" sender would mis-route the reply.
        sender = "a2a-" + (meta.get("from") or "client").replace(":", "-")
        text = _a2a_text(msg.get("parts"))
        mid = R.send(conn, sender, recipient, text)
        return ok({
            "id": f"a2a-{mid}",
            "contextId": msg.get("contextId") or f"ctx-{mid}",
            "status": {"state": "submitted"},
            "history": [msg],
        })

    if method == "tasks/get":
        tid = str(params.get("id") or "")
        tail = tid.rsplit("-", 1)[-1]
        if not tail.isdigit():
            return err(-32602, "bad task id")
        mid = int(tail)
        row = conn.execute("SELECT * FROM inbox WHERE id=?", (mid,)).fetchone()
        if not row:
            return err(-32001, "task not found")
        # replies are mail addressed back to the original A2A sender
        replies = conn.execute(
            "SELECT * FROM inbox WHERE recipient=? AND id>? ORDER BY id", (row["sender"], mid)
        ).fetchall()
        state = "completed" if replies else _A2A_STATE.get(row["status"], "unknown")
        artifacts = [{
            "artifactId": f"reply-{r['id']}", "name": "reply",
            "parts": [{"text": r["body"], "mediaType": "text/plain"}],
        } for r in replies]
        return ok({"id": tid, "contextId": row["topic"] or f"ctx-{mid}",
                   "status": {"state": state}, "artifacts": artifacts})

    return err(-32601, f"method not supported: {method}")


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

    def _authed(self):
        h = self.headers.get("Authorization", "")
        return h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip(), TOKEN)

    def do_GET(self):
        u = urlparse(self.path)
        if TOKEN and u.path not in OPEN_PATHS and not self._authed():
            return self._reply({"error": "unauthorized"}, 401)
        q = parse_qs(u.query)
        c = R.connect()
        if u.path == "/health":
            return self._reply({"ok": True, "db": R.DB_PATH})
        if u.path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            return self._reply(agent_card(c))
        if u.path == "/inbox":
            return self._reply({"messages": _rows(R.fetch_undelivered(c, q["node"][0]))})
        if u.path == "/thread":
            return self._reply({"messages": _rows(R.fetch_thread(c, q["node"][0]))})
        self._reply({"error": "not found"}, 404)

    def _a2a_stream(self, conn, rpc):
        """A2A message/stream over Server-Sent Events: submitted -> artifact(s)
        -> completed, as the target node replies. Falls to 'working' on timeout."""
        rid = rpc.get("id")
        params = rpc.get("params") or {}
        msg = params.get("message") or {}
        meta = msg.get("metadata") or params.get("metadata") or {}
        recipient = meta.get("recipient") or DEFAULT_NODE
        sender = "a2a-" + (meta.get("from") or "client").replace(":", "-")
        text = _a2a_text(msg.get("parts"))
        mid = R.send(conn, sender, recipient, text)
        tid, ctx = f"a2a-{mid}", (msg.get("contextId") or f"ctx-{mid}")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(result):
            self.wfile.write(f"data: {json.dumps({'jsonrpc':'2.0','id':rid,'result':result})}\n\n".encode())
            self.wfile.flush()

        try:
            emit({"id": tid, "contextId": ctx, "status": {"state": "submitted"}})
            deadline, seen = time.time() + 60, set()
            while time.time() < deadline:
                replies = conn.execute(
                    "SELECT * FROM inbox WHERE recipient=? AND id>? ORDER BY id", (sender, mid)
                ).fetchall()
                new = [r for r in replies if r["id"] not in seen]
                for r in new:
                    seen.add(r["id"])
                    emit({"taskId": tid, "contextId": ctx, "kind": "artifact-update",
                          "artifact": {"artifactId": f"reply-{r['id']}",
                                       "parts": [{"text": r["body"], "mediaType": "text/plain"}]}})
                if new:
                    emit({"taskId": tid, "contextId": ctx, "kind": "status-update",
                          "status": {"state": "completed"}, "final": True})
                    return
                time.sleep(0.5)
            emit({"taskId": tid, "contextId": ctx, "kind": "status-update",
                  "status": {"state": "working"}, "final": True})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        u = urlparse(self.path)
        if TOKEN and not self._authed():
            return self._reply({"error": "unauthorized"}, 401)
        d = self._json_body()
        c = R.connect()
        if u.path == "/a2a":
            if d.get("method") == "message/stream":
                return self._a2a_stream(c, d)
            return self._reply(a2a_rpc(c, d))
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
    ap.add_argument("--public-url", default=None,
                    help="how peers reach this sidecar (goes in the A2A Agent Card)")
    ap.add_argument("--default-node", default="mailroom",
                    help="A2A message recipient when none is given in metadata.recipient")
    ap.add_argument("--token", default=os.environ.get("ROOKERY_TOKEN"),
                    help="require Authorization: Bearer <token> on non-public endpoints "
                         "(default $ROOKERY_TOKEN). /health + agent-card stay public.")
    a = ap.parse_args()
    global PUBLIC_URL, DEFAULT_NODE, TOKEN
    PUBLIC_URL = a.public_url or f"http://{a.host}:{a.port}"
    DEFAULT_NODE = a.default_node
    TOKEN = a.token or None
    R.connect()  # ensure DB + schema exist
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    auth = "ON (Bearer)" if TOKEN else "OFF (open)"
    print(f"[mailroom] serving {R.DB_PATH} on http://{a.host}:{a.port}  auth={auth}", flush=True)
    if TOKEN and a.host == "0.0.0.0":
        print("[mailroom] NOTE: the token authenticates but does NOT encrypt. Over the internet, "
              "put this behind TLS or a tunnel (Tailscale/SSH/Cloudflare).", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
