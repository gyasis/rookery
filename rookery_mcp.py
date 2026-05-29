#!/usr/bin/env python3
"""Rookery MCP server — exposes the mesh to a Claude Code session (and its
subagents) as native tools, so the session can JOIN the mailroom: message other
agents, read its inbox, wait for replies, and discover the roster.

The CLI tools (send_mail.py / mailctl.py) remain the scripting path; this is the
tool-call path. Local mailroom by default; set ROOKERY_URL to use a remote
sidecar (with ROOKERY_TOKEN).

Wire into Claude Code via ~/.claude.json mcpServers:
  "rookery": {
    "command": "python3",
    "args": ["/home/gyasis/Documents/code/rookery/rookery_mcp.py"],
    "env": {"ROOKERY_NODE": "claude-main",
            "ROOKERY_DB": "/home/gyasis/Documents/code/rookery/rookery.db"}
  }
Env: ROOKERY_NODE (this session's node id, default 'claude-main'),
     ROOKERY_DB (local) or ROOKERY_URL + ROOKERY_TOKEN (remote sidecar).
"""
import os
import time

from mcp.server.fastmcp import FastMCP

import rookery as R

NODE = os.environ.get("ROOKERY_NODE", "claude-main")
URL = os.environ.get("ROOKERY_URL")  # if set, talk to a remote sidecar instead of the local DB


# --- backend: local DB or remote sidecar (same surface either way) --------
if URL:
    import mailctl  # reuses ROOKERY_TOKEN for the Authorization header

    def _send(to, body, topic=None):
        return mailctl.call(URL, "/send", "POST",
                            {"sender": NODE, "recipient": to, "body": body, "topic": topic})["id"]

    def _pending(node):
        return mailctl.call(URL, "/inbox?node=" + node, "GET")["messages"]

    def _consume(ids):
        mailctl.call(URL, "/claim", "POST", {"ids": ids})
        mailctl.call(URL, "/ack", "POST", {"ids": ids})

    def _register():
        mailctl.call(URL, "/register", "POST", {"node_id": NODE, "kind": "mcp"})

    def _roster():
        card = mailctl.call(URL, "/.well-known/agent-card.json", "GET")
        return [s["id"] for s in card.get("skills", [])]
else:
    def _send(to, body, topic=None):
        c = R.connect(); return R.send(c, NODE, to, body, topic)

    def _pending(node):
        c = R.connect(); return [dict(m) for m in R.fetch_undelivered(c, node)]

    def _consume(ids):
        c = R.connect(); R.claim(c, ids); R.ack(c, ids)

    def _register():
        c = R.connect(); R.register_node(c, NODE, kind="mcp")

    def _roster():
        c = R.connect()
        return [r["node_id"] for r in c.execute("SELECT node_id FROM nodes ORDER BY node_id")]


def _drain():
    """Return + consume new mail addressed to this session's node."""
    msgs = _pending(NODE)
    if msgs:
        _consume([m["id"] for m in msgs])
    return [{"from": m["sender"], "body": m["body"]} for m in msgs]


_register()
mcp = FastMCP("rookery")


@mcp.tool()
def send(to: str, body: str, topic: str = "") -> str:
    """Send a message to another agent/node in the Rookery mesh.
    `to` may be a node id, or node@mailroom for a federated mailroom."""
    mid = _send(to, body, topic or None)
    return f"sent #{mid}: {NODE} -> {to}"


@mcp.tool()
def check_inbox() -> list:
    """Return (and consume) any new messages addressed to this session."""
    return _drain()


@mcp.tool()
def await_message(timeout: float = 30.0) -> list:
    """Block up to `timeout` seconds for new mail to this session; return it
    (consumed). Empty list on timeout. Use after send() to get a reply."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = _drain()
        if got:
            return got
        time.sleep(1.0)
    return []


@mcp.tool()
def roster() -> list:
    """List the agents/nodes currently registered in the mesh."""
    return _roster()


if __name__ == "__main__":
    mcp.run()
