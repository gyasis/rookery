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

import mail_gate
import rookery as R
import turn_block

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
    import json
    import urllib.error

    import mailctl  # reuses ROOKERY_TOKEN for the Authorization header

    def _refusal(exc):
        """An HTTP error from the sidecar as a gate-style result (the stdio path returns text)."""
        reason = detail = None
        if isinstance(exc, urllib.error.HTTPError):
            try:
                body = json.loads(exc.read() or b"{}")
                reason, detail = body.get("error"), body.get("detail")
            except Exception:
                pass
            reason = reason or f"HTTP {exc.code}"
        else:
            reason = f"unreachable: {getattr(exc, 'reason', exc)}"
        return {"outcome": "refused", "reason": reason, "detail": detail}

    def _send(node, to, body, topic=None):
        try:
            return mailctl.call(
                URL,
                "/send",
                "POST",
                {"sender": node, "recipient": to, "body": body, "topic": topic},
            )
        except urllib.error.URLError as exc:      # HTTPError is a URLError
            return _refusal(exc)

    def _pending(node):
        return mailctl.call(URL, "/inbox?node=" + node, "GET")["messages"]

    def _ack(ids, node, ctx=None):
        # reads stay read-only (GET /inbox); this is the only call that marks mail read
        try:
            res = mailctl.call(URL, "/ack", "POST", {"node": node, "ids": ids})
        except urllib.error.URLError as exc:
            return {"ok": [], "already": [], "skipped": list(ids), "error": _refusal(exc)["reason"]}
        return {k: res.get(k, []) for k in ("ok", "already", "skipped")}

    def _roster():
        card = mailctl.call(URL, "/.well-known/agent-card.json", "GET")
        return [s["id"] for s in card.get("skills", [])]

else:

    def _pending(node, ctx=None):
        """Unread mail for the caller, with bodies. Reads only (FR-032).
        stdio: the session identity, else legacy mail to the node name.
        Shared HTTP: no principal, so mail to the asked node name in any session (`unread_by_name`),
        the rows the mailroom's /inbox shows for that name. A session filter would hide every
        session mail from a caller that has no identity."""
        c = R.connect()
        try:
            if HTTP:
                return mail_gate.unread_by_name(c, node, with_body=True)
            who = mail_gate.principal_from_env(c, os.environ, _root())
            if who is None:
                return mail_gate.unread_legacy(c, node, with_body=True)
            return mail_gate.unread(c, who[1], with_body=True)
        finally:
            c.close()

    def _ack(ids, node, ctx=None):
        c = R.connect()
        try:
            if HTTP:
                return mail_gate.ack_by_name(c, node, ids, from_inflight=False)
            who = mail_gate.principal_from_env(c, os.environ, _root())
            if who is None:
                return mail_gate.ack_legacy(c, node, ids)
            return mail_gate.ack(c, who[1], ids)
        finally:
            c.close()

    def _roster():
        c = R.connect()
        return [
            r["node_id"]
            for r in c.execute("SELECT node_id FROM nodes ORDER BY node_id")
        ]


def _node(ctx: Context) -> str:
    """This connection's node id (see SHARED HTTP MODE); stdio keeps NODE."""
    if not HTTP:
        return NODE
    req = ctx.request_context.request
    hdr = req.headers if req is not None else {}
    node = hdr.get("x-rookery-node") or (
        f"claude-{HOST}-{(hdr.get('mcp-session-id') or 'nosession')[:8]}")
    return node


def _asked_node(ctx: Context):
    """Shared HTTP mode: the node name the caller asked for in `x-rookery-node`, else None."""
    req = ctx.request_context.request
    return (req.headers.get("x-rookery-node") if req is not None else None) or None


def _root():
    return turn_block.gate_root()          # the gate log lives in one place (D15)


def _read(node):
    """Unread mail for this session's node. Never marks anything read (FR-032)."""
    return [{"id": m["id"], "from": m.get("sender_name") or m["sender"], "body": m["body"]}
            for m in _pending(node)]


mcp = FastMCP("rookery")


def _remote_follow():
    """A remote send succeeded: start the bounded follower for this node. Never changes the result."""
    try:
        node = os.environ.get("ROOKERY_NODE")
        if not node or os.environ.get("ROOKERY_WAKE") == "off":
            return
        import rookery_remote
        rookery_remote.start_follow(node)
    except Exception:
        pass


@mcp.tool()
def send(to: str, body: str, ctx: Context, topic: str = "") -> str:
    """Send a message to another agent/node in the Rookery mesh.
    `to` may be a node id, or node@mailroom for a federated mailroom."""
    node = _node(ctx)
    if URL:                                # remote sidecar: it runs the gate
        res = _send(node, to, body, topic or None)
        if res.get("outcome", "sent") == "sent":
            _remote_follow()
            return f"sent #{res.get('id')}: {node} -> {to}"
        return f"{res['outcome']}: {res.get('reason')}"
    conn = R.connect()
    try:
        root = _root()
        # stdio: the caller is the session identity. Shared HTTP: no principal; the header
        # names the sender (asked_sender), as before.
        principal = None if HTTP else mail_gate.principal_from_env(conn, os.environ, root)
        res = mail_gate.submit(conn, path="mcp-http" if HTTP else "mcp-stdio", principal=principal,
                               raw_sender=node, to=to, body=body, topic=topic,
                               asked_sender=_asked_node(ctx) if HTTP else None, root=root)
    finally:
        conn.close()
    if res["outcome"] == "sent":
        return f"sent #{res['msg_id']}: {node} -> {to}"
    return f"{res['outcome']}: {res['reason']}"


@mcp.tool()
def check_inbox(ctx: Context) -> str:
    """List the unread messages addressed to this session. Changes nothing: the mail stays
    unread until you call ack(ids)."""
    msgs = _read(_node(ctx))
    if not msgs:
        return "No unread mail."
    lines = [f"#{m['id']} from {m['from']}: {m['body']}" for m in msgs]
    lines.append("Mark read: ack([%s])" % ", ".join(str(m["id"]) for m in msgs))
    return "\n".join(lines)


@mcp.tool()
async def await_message(ctx: Context, timeout: float = 30.0) -> list:
    """Block up to `timeout` seconds for unread mail to this session; return it (still unread:
    call ack(ids) to mark it read). Empty list on timeout. Use after send() to get a reply."""
    # async + asyncio.sleep: a sync sleep here would stall the shared server's
    # event loop, freezing every OTHER session's calls for up to `timeout`.
    node = _node(ctx)
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = _read(node)
        if got:
            return got
        await asyncio.sleep(1.0)
    return []


@mcp.tool()
def ack(ids: list[int], ctx: Context) -> dict:
    """Mark mail read, by the ids that check_inbox / await_message showed. Returns the ids that
    were marked (ok), were already read (already), or are not yours to ack (skipped)."""
    res = _ack(list(ids), _node(ctx))
    return {k: list(res.get(k, [])) for k in ("ok", "already", "skipped")}


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
