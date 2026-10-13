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

SHARED HTTP MODE (2026-10-13): `rookery_mcp.py --http 7702` runs ONE server for
every Claude session (systemd --user rookery-mcp.service) instead of one stdio
copy per session — 23 copies for 23 sessions before this. Identity can no longer
come from the process, so each MCP connection gets its own node:
  · an `X-Rookery-Node` request header, if the client sends one, else
  · claude-<host>-<first 8 of the Mcp-Session-Id>, stable for the connection.
Same semantics as the per-process default it replaces: unique per session.
"""

import asyncio
import os
import socket
import sys
import time

from mcp.server.fastmcp import Context, FastMCP

import rookery as R

# Node identity: an explicit ROOKERY_NODE always wins. Otherwise fall back to a
# unique-per-process id so two MCP sessions on one host never silently collide
# (the old default pinned every session to "claude-main").
HOST = socket.gethostname().split('.')[0]
NODE = os.environ.get("ROOKERY_NODE") or f"claude-{HOST}-{os.getpid()}"
HTTP = "--http" in sys.argv
URL = os.environ.get(
    "ROOKERY_URL"
)  # if set, talk to a remote sidecar instead of the local DB


# --- backend: local DB or remote sidecar (same surface either way) --------
if URL:
    import mailctl  # reuses ROOKERY_TOKEN for the Authorization header

    def _send(node, to, body, topic=None):
        return mailctl.call(
            URL,
            "/send",
            "POST",
            {"sender": node, "recipient": to, "body": body, "topic": topic},
        )["id"]

    def _pending(node):
        return mailctl.call(URL, "/inbox?node=" + node, "GET")["messages"]

    def _consume(ids):
        mailctl.call(URL, "/claim", "POST", {"ids": ids})
        mailctl.call(URL, "/ack", "POST", {"ids": ids})

    def _register(node):
        mailctl.call(URL, "/register", "POST", {"node_id": node, "kind": "mcp"})

    def _roster():
        card = mailctl.call(URL, "/.well-known/agent-card.json", "GET")
        return [s["id"] for s in card.get("skills", [])]

else:

    def _send(node, to, body, topic=None):
        c = R.connect()
        return R.send(c, node, to, body, topic)

    def _pending(node):
        c = R.connect()
        return [dict(m) for m in R.fetch_undelivered(c, node)]

    def _consume(ids):
        c = R.connect()
        R.claim(c, ids)
        R.ack(c, ids)

    def _register(node):
        c = R.connect()
        R.register_node(c, node, kind="mcp")

    def _roster():
        c = R.connect()
        return [
            r["node_id"]
            for r in c.execute("SELECT node_id FROM nodes ORDER BY node_id")
        ]


_seen: set[str] = set()


def _node(ctx: Context) -> str:
    """This connection's node id (see SHARED HTTP MODE); stdio keeps NODE."""
    if not HTTP:
        return NODE
    req = ctx.request_context.request
    hdr = req.headers if req is not None else {}
    node = hdr.get("x-rookery-node") or (
        f"claude-{HOST}-{(hdr.get('mcp-session-id') or 'nosession')[:8]}")
    if node not in _seen:                  # register on first use, like startup did
        _register(node)
        _seen.add(node)
    return node


def _drain(node):
    """Return + consume new mail addressed to this session's node."""
    msgs = _pending(node)
    if msgs:
        _consume([m["id"] for m in msgs])
    return [{"from": m["sender"], "body": m["body"]} for m in msgs]


if not HTTP:
    _register(NODE)
mcp = FastMCP("rookery")


@mcp.tool()
def send(to: str, body: str, ctx: Context, topic: str = "") -> str:
    """Send a message to another agent/node in the Rookery mesh.
    `to` may be a node id, or node@mailroom for a federated mailroom."""
    node = _node(ctx)
    mid = _send(node, to, body, topic or None)
    return f"sent #{mid}: {node} -> {to}"


@mcp.tool()
def check_inbox(ctx: Context) -> list:
    """Return (and consume) any new messages addressed to this session."""
    return _drain(_node(ctx))


@mcp.tool()
async def await_message(ctx: Context, timeout: float = 30.0) -> list:
    """Block up to `timeout` seconds for new mail to this session; return it
    (consumed). Empty list on timeout. Use after send() to get a reply."""
    # async + asyncio.sleep: a sync sleep here would stall the shared server's
    # event loop, freezing every OTHER session's calls for up to `timeout`.
    node = _node(ctx)
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = _drain(node)
        if got:
            return got
        await asyncio.sleep(1.0)
    return []


@mcp.tool()
def roster() -> list:
    """List the agents/nodes currently registered in the mesh."""
    return _roster()


if __name__ == "__main__":
    if HTTP:
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = int(sys.argv[sys.argv.index("--http") + 1])
        mcp.run(transport="streamable-http")      # served at /mcp
    else:
        mcp.run()
