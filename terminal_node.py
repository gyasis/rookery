#!/usr/bin/env python3
"""Rookery terminal node (substrate A) — bridge a human (or an off-the-shelf
terminal agent: Claude Code, aider, codex CLI…) sitting in a terminal PANE into
the mesh. A background watcher polls this node's inbox and INJECTS new mail into
the pane; the person/agent replies with the CLI:
    python3 send_mail.py --from <node-id> --to <other> "..."

Pane control is PLUGGABLE — not tmux-locked. All three are "type into a pane":
    --injector tmux     tmux send-keys -t <target>              (C)
    --injector wezterm  wezterm cli send-text --pane-id <t>     (Rust)
    --injector zellij   zellij action write-chars               (Rust)

Policy screening (security.inspect_inbound) applies here too — a blocked message
is never injected into the pane.
"""
import argparse
import subprocess
import time

import rookery as R
from node_runner import log, screen


def inject(injector, target, text):
    """Type `text` (+ Enter) into the target pane via the chosen multiplexer."""
    if injector == "tmux":
        subprocess.run(["tmux", "send-keys", "-t", target, "-l", text], check=False)
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=False)
    elif injector == "wezterm":
        subprocess.run(["wezterm", "cli", "send-text", "--no-paste",
                        "--pane-id", target, text + "\n"], check=False)
    elif injector == "zellij":
        subprocess.run(["zellij", "action", "write-chars", text], check=False)
        subprocess.run(["zellij", "action", "write", "10"], check=False)  # newline
    else:
        raise SystemExit(f"unknown injector: {injector}")


def run(node_id, injector, target, poll):
    conn = R.connect()
    R.register_node(conn, node_id, kind="terminal")
    conn.execute("UPDATE nodes SET address=? WHERE node_id=?", (target, node_id))
    conn.commit()
    log(node_id, f"terminal node online — injecting via {injector} into '{target}'. "
                 f"Reply with: python3 send_mail.py --from {node_id} --to <x> '...'")
    try:
        while True:
            R.heartbeat(conn, node_id)
            msgs = R.fetch_undelivered(conn, node_id)
            if msgs:
                msgs = screen(conn, node_id, msgs)  # policy vets before the pane sees it
            if msgs:
                R.claim(conn, [m["id"] for m in msgs])
                for m in msgs:
                    inject(injector, target, f"<ROOKERY> mail from {m['sender']}: {m['body']}")
                    log(node_id, f"injected #{m['id']} from {m['sender']}")
                R.ack(conn, [m["id"] for m in msgs])
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        R.set_status(conn, node_id, "offline")


def main():
    ap = argparse.ArgumentParser(description="Run a Rookery terminal node (pane bridge).")
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--injector", choices=["tmux", "wezterm", "zellij"], default="tmux")
    ap.add_argument("--target", required=True, help="pane target (tmux session:win.pane / wezterm pane-id)")
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    run(a.node_id, a.injector, a.target, a.poll)


if __name__ == "__main__":
    main()
