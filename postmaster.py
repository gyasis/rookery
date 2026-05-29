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
import subprocess
import sys
import time

import rookery as R

_HERE = os.path.dirname(os.path.abspath(__file__))
NODE_RUNNER = os.path.join(_HERE, "node_runner.py")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] (postmaster) {msg}", flush=True)


def has_mail(conn, node):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM inbox WHERE recipient=? AND delivered=0", (node,)
    ).fetchone()
    return row["c"] > 0


def wake(node, engine, lifecycle, idle_timeout, allowed_tools="", model=None):
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


def run(node_engine, persistent_set, max_warm, idle_timeout, poll, allowed_tools="", model=None):
    conn = R.connect()
    nodes = list(node_engine)

    def lifecycle(n):
        return "persistent" if n in persistent_set else "ephemeral"

    log(f"online. managing {node_engine}. persistent={sorted(persistent_set)} "
        f"(max_warm={max_warm}, warm window={idle_timeout:g}s). agents sleep until mail.")
    running = {}        # node_id -> Popen (at most one live turn per node)
    woke_at = {}        # node_id -> last wake time (for warm-pool LRU eviction)
    granted_seen = set()
    try:
        while True:
            # 1. reap finished turns (ephemeral one-shots, or persistent warm windows that timed out)
            for n, p in list(running.items()):
                if p.poll() is not None:
                    del running[n]
                    log(f"'{n}' finished its turn -> asleep ($0)")

            # 2. approved credentials -> mail the grant to the waiting node
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

            # 3. wake any managed node that has mail and isn't already running
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
                                  allowed_tools, model)
                woke_at[n] = time.time()

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
    ap.add_argument("--engine", choices=["mock", "claude", "codex"], default="mock",
                    help="default engine for roster entries with no =engine")
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
        allowed_tools=a.allowed_tools, model=a.model)


if __name__ == "__main__":
    main()
