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


def wake(node, engine):
    proc = subprocess.Popen(
        [sys.executable, NODE_RUNNER, "--node-id", node,
         "--engine", engine, "--once", "--managed"]
    )
    log(f"mail for '{node}' -> woke a one-shot turn (pid {proc.pid})")
    return proc


def run(nodes, engine, poll):
    conn = R.connect()
    log(f"online. managing {nodes}. agents stay asleep until mail arrives.")
    running = {}        # node_id -> Popen (at most one live turn per node)
    granted_seen = set()
    try:
        while True:
            # 1. reap finished one-shot turns
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
                if n not in running and has_mail(conn, n):
                    running[n] = wake(n, engine)

            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        for p in running.values():
            p.wait()
        log("offline")


def main():
    ap = argparse.ArgumentParser(description="Run the Rookery central postmaster.")
    ap.add_argument("--nodes", required=True,
                    help="comma-separated roster to manage, e.g. architect,triage")
    ap.add_argument("--engine", choices=["mock", "claude"], default="mock")
    ap.add_argument("--poll", type=float, default=0.5)
    a = ap.parse_args()
    roster = [x.strip() for x in a.nodes.split(",") if x.strip()]
    run(roster, a.engine, a.poll)


if __name__ == "__main__":
    main()
