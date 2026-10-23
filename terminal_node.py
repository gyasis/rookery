#!/usr/bin/env python3
"""Rookery terminal node (substrate A) — bridge a human (or an off-the-shelf
terminal agent: Claude Code, aider, codex CLI…) sitting in a terminal PANE into
the mesh. A background watcher polls this node's inbox and INJECTS new mail into
the pane a one-line NOTICE (sender, topic, `rookery inbox`) - never the body, and the mail
stays pending until read; the person/agent replies with the CLI:
    rookery send --from <node-id> --to <other> "..."

Pane control is PLUGGABLE — not tmux-locked. Three built in, all "type into a pane":
    --injector tmux     tmux send-keys -t <target>              (C)
    --injector wezterm  wezterm cli send-text --pane-id <t>     (Rust)
    --injector zellij   zellij action write-chars               (Rust)

Plugins may contribute more (see plugins.py); `--help` lists whatever is
attached on this machine. Core does not know their names in advance.

Policy screening (security.inspect_inbound) applies here too — a blocked message
is never injected into the pane.
"""
import argparse
import os
import subprocess
import time

import plugins
import postmaster
import rookery as R
from node_runner import log, screen

BUILTIN_INJECTORS = ("tmux", "wezterm", "zellij")


def inject(injector, target, text):
    """Type `text` (+ Enter) into the target pane via the chosen multiplexer."""
    if injector not in BUILTIN_INJECTORS:
        entry = plugins.get_injector(injector)
        if entry is None:
            raise SystemExit(f"unknown injector: {injector}")
        entry["send"](target, text)
    elif injector == "tmux":
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


def _pass(conn, node_id, injector, target, root, seen=None):
    """One poll: type ONE notice for pending mail not yet announced, never the body, never mark it read.
    Announced ids live in the postmaster pushed set, so a mail gets one notice across this node and
    postmaster.wake_identities. `seen` (kept by run()) also holds them, so a node with no root does
    not type the same notice on every poll. Mail that postmaster would not push gets no notice."""
    R.heartbeat(conn, node_id)
    msgs = R.fetch_undelivered(conn, node_id)
    if msgs:
        msgs = screen(conn, node_id, msgs)  # policy vets before the pane sees it
    if not msgs:
        return 0
    pending_ids = [m["id"] for m in msgs]
    done, last = postmaster._load_pushed(root, node_id) if root else (None, 0)
    if done is None:
        done = {i for i in pending_ids if i <= last}
    if seen is not None:
        seen.intersection_update(pending_ids)       # forget mail that is no longer pending
        done = done | seen
    fresh = [m for m in msgs if m["id"] not in done and postmaster._needs_push(m, node_id, conn)]
    if not fresh:
        return 0
    ident = conn.execute("SELECT name FROM identities WHERE agent_id=?", (node_id,)).fetchone()
    inject(injector, target, postmaster._notice(conn, {"name": ident["name"] if ident else node_id}, fresh))
    log(node_id, f"noticed {[m['id'] for m in fresh]}")
    if seen is not None:
        seen.update(m["id"] for m in fresh)
    if root:
        announced = done | {m["id"] for m in fresh}
        postmaster._write_pushed(root, node_id, pending_ids, [i for i in pending_ids if i in announced])
    return len(fresh)


def run(node_id, injector, target, poll, once=False, root=None):
    entry = plugins.get_injector(injector)
    if entry and entry["check"]:
        # A plugin injector is an explicit opt-in, so let it fail loudly rather
        # than silently deliver nothing.
        entry["check"]()
    root = root or os.environ.get("ROOKERY_ROOT")
    conn = R.connect()
    R.register_node(conn, node_id, kind="terminal")
    conn.execute("UPDATE nodes SET address=? WHERE node_id=?", (target, node_id))
    conn.commit()
    log(node_id, f"terminal node online - noticing via {injector} into '{target}'. "
                 f"Reply with: rookery send --from {node_id} --to <x> '...'")
    seen = set()                                    # announced ids, also without a root
    try:
        while True:
            _pass(conn, node_id, injector, target, root, seen)
            if once:
                break
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        R.set_status(conn, node_id, "offline")


def main():
    ap = argparse.ArgumentParser(description="Run a Rookery terminal node (pane bridge).")
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--injector",
                    choices=list(BUILTIN_INJECTORS) + plugins.injectors(),
                    default="tmux",
                    help="pane-injection mechanism; plugin-contributed ones are "
                         "listed only when their tool is installed")
    ap.add_argument("--target", required=True,
                    help="pane target, in whatever form the chosen injector "
                         "addresses (e.g. tmux session:win.pane, wezterm pane-id)")
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    run(a.node_id, a.injector, a.target, a.poll)


if __name__ == "__main__":
    main()
