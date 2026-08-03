#!/usr/bin/env python3
"""Rookery postmaster — the central watcher (mode B, additive to v1).

The agents stay ASLEEP. This is the only always-on process. It is cheap and
non-LLM: it polls the whole mailroom and, when mail lands for a managed node,
it WAKES that node by spawning a single one-shot turn
(`node_runner --once --managed`). The node processes its mail and exits — back
to $0, nothing running. When a credential request is approved, the postmaster
mails the grant to the waiting node, which naturally wakes it.

This is the "polling agent that says 'you've got mail' and wakes the agent"
model. v1 (self-polling nodes) is untouched; this is the richer orchestration
where agents can be truly paused and still get driven.

  $0 while asleep · survives reboot (state is in the mailroom) · one poller, N
  sleeping agents.
"""
import argparse
import os
import re
import subprocess
import sys
import time

import plugins
import rookery as R

_MENTION = re.compile(r"@([A-Za-z0-9_-]+)")

_HERE = os.path.dirname(os.path.abspath(__file__))
NODE_RUNNER = os.path.join(_HERE, "node_runner.py")
SDK_NODE = os.path.join(_HERE, "sdk_node.py")
# A node heartbeats while it works; requeue its in-flight mail only after it has
# been silent this long (genuinely dead) — NOT because a turn is slow.
NODE_DEAD_AFTER = 60


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] (postmaster) {msg}", flush=True)


def has_mail(conn, node):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM inbox WHERE recipient=? AND status='pending'", (node,)
    ).fetchone()
    return row["c"] > 0


def wake(node, engine, lifecycle, idle_timeout, allowed_tools="", model=None, mcp=""):
    if engine == "claude-sdk":
        # warm long-lived Claude session (hooks off) — kills the ~115s cold start.
        cmd = [sys.executable, SDK_NODE, "--node-id", node,
               "--idle-timeout", str(idle_timeout), "--poll", "1"]
        if allowed_tools:
            cmd += ["--allowed-tools", allowed_tools]
        if model:
            cmd += ["--model", model]
        if mcp:
            cmd += ["--mcp", mcp]
        proc = subprocess.Popen(cmd)
        log(f"mail for '{node}' -> woke a WARM SDK session (window {idle_timeout:g}s, pid {proc.pid})")
        return proc
    cmd = [sys.executable, NODE_RUNNER, "--node-id", node, "--engine", engine, "--managed"]
    if lifecycle == "persistent":
        # warm window: self-polls and self-reaps after idle_timeout; rehydrates from DB.
        cmd += ["--lifecycle", "persistent", "--idle-timeout", str(idle_timeout)]
    else:
        cmd += ["--once"]   # ephemeral: one turn then gone, no memory
    if allowed_tools:
        cmd += ["--allowed-tools", allowed_tools]   # only claude nodes use it
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd)
    tag = (f"WARM persistent (window {idle_timeout:g}s)"
           if lifecycle == "persistent" else "one-shot ephemeral")
    log(f"mail for '{node}' -> woke a {tag} turn (pid {proc.pid})")
    return proc


def run(node_engine, persistent_set, max_warm, idle_timeout, poll, allowed_tools="", model=None,
        mcp="", notify_events="needcred"):
    conn = R.connect()
    nodes = list(node_engine)

    def lifecycle(n):
        # claude-sdk nodes are inherently warm/persistent (they hold a live session)
        if node_engine.get(n) == "claude-sdk" or n in persistent_set:
            return "persistent"
        return "ephemeral"

    # register the roster up front so discovery (the A2A agent card) lists them
    for n in nodes:
        kind = "sdk" if node_engine[n] == "claude-sdk" else node_engine[n]
        R.register_node(conn, n, kind=kind, lifecycle=lifecycle(n))
        R.set_status(conn, n, "offline")

    log(f"online. managing {node_engine}. persistent={sorted(persistent_set)} "
        f"(max_warm={max_warm}, warm window={idle_timeout:g}s). agents sleep until mail.")
    running = {}        # node_id -> Popen (at most one live turn per node)
    woke_at = {}        # node_id -> last wake time (for warm-pool LRU eviction)
    granted_seen = set()
    needcred_seen = set()
    last_mention_id = 0
    try:
        while True:
            # 0. durability: return mail claimed by a node that has gone SILENT
            #    (dead) — a slow-but-heartbeating node keeps its claim.
            requeued = R.requeue_stale(conn, NODE_DEAD_AFTER)
            if requeued:
                log(f"requeued {requeued} in-flight message(s) (claiming node went silent)")

            # 1. reap finished turns (ephemeral one-shots, or persistent warm windows that timed out)
            for n, p in list(running.items()):
                if p.poll() is not None:
                    del running[n]
                    log(f"'{n}' finished its turn -> asleep ($0)")

            # 2. NEEDCRED -> raise it through whatever alert sinks are attached.
            #    A credential phone-home IS "an agent stuck waiting on a human".
            #    With no plugin attached there is no sink, notify() returns 0,
            #    and the request stays exactly as visible as it always was —
            #    which is the standalone behaviour, not a degraded one.
            if notify_events != "none":
                for r in conn.execute(
                    "SELECT * FROM credential_requests WHERE status='pending'"
                ).fetchall():
                    if r["id"] in needcred_seen:
                        continue
                    needcred_seen.add(r["id"])
                    addr = R.get_address(conn, r["node_id"])
                    where = f" [{addr}]" if addr else ""
                    fired = plugins.notify(
                        "needcred",
                        f"NEEDCRED — {r['node_id']} is blocked",
                        f"wants {r['resource']}{where}. Approve: "
                        f"mesh_approve.py --id {r['id']} --approve",
                        node=r["node_id"], resource=r["resource"], request_id=r["id"],
                    )
                    if fired:
                        log(f"credential #{r['id']} ('{r['node_id']}' -> "
                            f"{r['resource']}) -> raised on {fired} sink(s)")

            # 3. approved credentials -> mail the grant to the waiting node
            for r in conn.execute(
                "SELECT * FROM credential_requests WHERE status='approved'"
            ).fetchall():
                if r["id"] in granted_seen:
                    continue
                granted_seen.add(r["id"])
                if r["node_id"] in nodes:
                    R.send(conn, "postmaster", r["node_id"],
                           f"CRED_GRANTED:{r['resource']}:{r['token_ref']}")
                    log(f"credential #{r['id']} approved -> mailed grant to '{r['node_id']}'")

            # 4. "they're talking about you": notify a managed node @mentioned in
            #    new mail it isn't the recipient of (scan each message once).
            for r in conn.execute(
                "SELECT * FROM inbox WHERE id > ? ORDER BY id", (last_mention_id,)
            ).fetchall():
                last_mention_id = max(last_mention_id, r["id"])
                body = r["body"] or ""
                # skip postmaster notices and acks (acks often echo the original
                # text, which would re-trigger the same mention)
                if r["sender"] == "postmaster" or body.startswith("ACK:"):
                    continue
                for m in set(_MENTION.findall(body)):
                    if m in nodes and m != r["recipient"] and m != r["sender"]:
                        R.send(conn, "postmaster", m,
                               f"MENTION by {r['sender']} (in mail to {r['recipient']}): "
                               f"{(r['body'] or '')[:140]}")
                        log(f"@{m} mentioned by {r['sender']} -> notified")

            # 5. wake any managed node that has mail and isn't already running
            for n in nodes:
                if n in running or not has_mail(conn, n):
                    continue
                if lifecycle(n) == "persistent":
                    warm = [x for x in running if lifecycle(x) == "persistent"]
                    if len(warm) >= max_warm:
                        # warm pool full -> evict the least-recently-woken partner.
                        # Its context is safe in the DB; it rehydrates when next needed.
                        victim = min(warm, key=lambda x: woke_at.get(x, 0))
                        log(f"warm pool full ({len(warm)}/{max_warm}) -> evicting '{victim}' (rehydrates later)")
                        running[victim].terminate()
                        continue   # free the slot this tick; wake n next tick
                running[n] = wake(n, node_engine[n], lifecycle(n), idle_timeout,
                                  allowed_tools, model, mcp)
                woke_at[n] = time.time()
                if notify_events == "all":
                    # Opt-in only: under a fan-out this is one alert per wake.
                    plugins.notify("mail", f"mail for {n}",
                                   f"{n} woke to handle its inbox", node=n)

            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        for p in running.values():
            p.terminate()
        log("offline")


def main():
    ap = argparse.ArgumentParser(description="Run the Rookery central postmaster.")
    ap.add_argument("--nodes", required=True,
                    help="roster, comma-separated. Per-node engine with "
                         "name=engine, e.g. architect=claude,reviewer=codex")
    ap.add_argument("--engine", choices=["mock", "claude", "codex", "gemini", "claude-sdk"],
                    default="mock",
                    help="default engine for roster entries with no =engine "
                         "(claude-sdk = warm long-lived session, no cold start)")
    ap.add_argument("--persistent", default="",
                    help="comma list of nodes that are persistent power-partners "
                         "(rehydrate from DB + join the warm pool); others are ephemeral")
    ap.add_argument("--max-warm", type=int, default=5,
                    help="max concurrently-warm persistent agents (the warm pool cap)")
    ap.add_argument("--idle-timeout", type=float, default=600.0,
                    help="persistent warm window in seconds before a partner sleeps ($0)")
    ap.add_argument("--allowed-tools", default="",
                    help="MCP tools claude nodes may fire, e.g. mcp__deeplakesearch__retrieve_context")
    ap.add_argument("--model", default=None, help="claude model override for all claude nodes")
    ap.add_argument("--mcp", default="",
                    help="comma list of MCP servers (from ~/.claude.json) to attach to claude-sdk nodes")
    ap.add_argument("--notify", dest="notify_events",
                    choices=["needcred", "all", "none"], default="needcred",
                    help="what to raise through attached alert sinks (see "
                         "plugins.py): needcred (default) = only credential "
                         "requests waiting on a human; all = also every mail wake "
                         "(one alert per wake — noisy under fan-out); none = off. "
                         "A no-sink install is unaffected either way.")
    ap.add_argument("--poll", type=float, default=0.5)
    a = ap.parse_args()
    node_engine = {}
    for entry in a.nodes.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            name, eng = entry.split("=", 1)
            node_engine[name.strip()] = eng.strip()
        else:
            node_engine[entry] = a.engine
    persistent_set = {x.strip() for x in a.persistent.split(",") if x.strip()}
    run(node_engine, persistent_set, a.max_warm, a.idle_timeout, a.poll,
        allowed_tools=a.allowed_tools, model=a.model, mcp=a.mcp,
        notify_events=a.notify_events)


if __name__ == "__main__":
    main()
