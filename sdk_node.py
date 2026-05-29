#!/usr/bin/env python3
"""Rookery SDK node — a PERSISTENT warm partner backed by a long-lived Claude
session (Claude Agent SDK).

Why: plain `claude -p` pays a ~115s startup every call (SessionStart hooks). A
ClaudeSDKClient opens the session ONCE; every subsequent mail turn reuses the
warm session, so turns 2..N are just inference. The SDK also doesn't load the
user's filesystem settings by default, so the heavy SessionStart hooks are
skipped — the one-time open is cheap too.

Lifecycle: persistent. Holds the warm session for an idle window; when the
window passes it closes the session and exits ($0). A fresh wake re-opens and
rehydrates the thread from the DB on its first turn (the warm session itself
carries memory for turns 2..N, so no re-send).
"""
import argparse
import asyncio
import json
import os
import time

import rookery as R
from node_runner import handle_directives, _mesh_prompt, log, screen

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
)


def load_mcp_servers(names):
    """Selectively re-add named MCP servers from ~/.claude.json. setting_sources=[]
    drops global MCP (to skip hooks), so a tool-using SDK node passes the few
    servers it needs explicitly — hooks stay off, only these servers load."""
    if not names:
        return {}
    try:
        d = json.load(open(os.path.expanduser("~/.claude.json")))
    except Exception:
        return {}
    pool = dict(d.get("mcpServers") or {})
    for pdata in (d.get("projects") or {}).values():
        pool.update((pdata or {}).get("mcpServers") or {})
    out = {}
    for n in names:
        cfg = pool.get(n)
        if not cfg:
            continue
        cfg = dict(cfg)
        if "type" not in cfg:
            cfg["type"] = "stdio" if cfg.get("command") else ("http" if cfg.get("url") else "stdio")
        out[n] = cfg
    return out


async def collect_text(client) -> str:
    parts = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts)


async def run(node_id, poll, idle_timeout, max_wait, allowed_tools, model, mcp_servers=None):
    conn = R.connect()
    R.register_node(conn, node_id, kind="sdk", pid=os.getpid(), lifecycle="persistent")

    opts = {
        "system_prompt": "You are a node in a multi-agent mesh. Keep replies short.",
        # [] => SDK passes --setting-sources= (empty): skip the user's SessionStart
        # hooks (the ~115s cost) and filesystem settings. Lean, fast worker.
        "setting_sources": [],
    }
    if allowed_tools:
        opts["allowed_tools"] = allowed_tools
        opts["permission_mode"] = "acceptEdits"
    if model:
        opts["model"] = model
    if mcp_servers:
        # re-add only the servers this node needs (hooks still off)
        opts["mcp_servers"] = mcp_servers
        log(node_id, f"MCP servers attached: {list(mcp_servers)}")
    options = ClaudeAgentOptions(**opts)

    log(node_id, "opening warm Claude session (SDK) — startup paid ONCE…")
    t0 = time.time()
    async with ClaudeSDKClient(options=options) as client:
        log(node_id, f"warm session ready in {time.time() - t0:.0f}s. watching for mail…")
        turn_no = 0
        first = True
        idle = 0.0
        while True:
            R.heartbeat(conn, node_id)
            R.set_status(conn, node_id, "idle")
            new = R.fetch_undelivered(conn, node_id)
            if new:
                new = screen(conn, node_id, new)  # policy vets inbound
            if not new:
                if idle_timeout and idle >= idle_timeout:
                    log(node_id, f"idle {int(idle)}s — closing warm session, sleeping ($0)")
                    break
                await asyncio.sleep(poll)
                idle += poll
                continue
            idle = 0.0
            R.set_status(conn, node_id, "busy")
            if first:
                thread = R.fetch_thread(conn, node_id)
                new_ids = {m["id"] for m in new}
                history = [m for m in thread if m["id"] not in new_ids]
                if history:
                    log(node_id, f"rehydrated {len(history)} prior msg(s) into the fresh session")
                prompt = _mesh_prompt(node_id, new, history)
                first = False
            else:
                prompt = _mesh_prompt(node_id, new, None)  # warm session already remembers
            ids = [m["id"] for m in new]
            R.claim(conn, ids)  # in-flight: requeued if we crash before ack
            turn_no += 1
            t = time.time()
            log(node_id, f"woke on {len(new)} message(s) -- unprompted (turn #{turn_no})")
            await client.query(prompt)
            text = await collect_text(client)
            kind = "COLD first turn" if turn_no == 1 else "WARM turn"
            log(node_id, f"turn #{turn_no} done in {time.time() - t:.0f}s ({kind})")
            paused, _ = handle_directives(conn, node_id, text, poll, max_wait, managed=True)
            R.ack(conn, ids)  # turn handled these -> done
            if paused:
                break
    R.set_status(conn, node_id, "offline")
    log(node_id, "offline")


def main():
    ap = argparse.ArgumentParser(description="Run a Rookery warm SDK partner node.")
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--idle-timeout", type=float, default=600.0)
    ap.add_argument("--max-wait", type=int, default=0)
    ap.add_argument("--allowed-tools", default="")
    ap.add_argument("--model", default=None)
    ap.add_argument("--mcp", default="",
                    help="comma list of MCP server names from ~/.claude.json to attach "
                         "(e.g. deeplakesearch)")
    a = ap.parse_args()
    allowed = [t for t in a.allowed_tools.replace(",", " ").split() if t]
    mcp = load_mcp_servers([s.strip() for s in a.mcp.split(",") if s.strip()])
    asyncio.run(run(a.node_id, a.poll, a.idle_timeout, a.max_wait, allowed, a.model, mcp))


if __name__ == "__main__":
    main()
